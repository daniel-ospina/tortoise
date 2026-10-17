"""#8051 — `refs/stash` is SHARED across worktrees: a lane must never take a peer's entry.

Hermetic: a real temp git repo with two REAL linked worktrees, real `git stash`
calls, no network, no Docker, no FalkorDB. Nothing is monkeypatched — the
property under test IS git's behaviour plus the tool's contract around it.

The load-bearing test is :func:`test_acceptance_lane_a_never_applies_lane_bs_entry`:
lane A stashes, lane B stashes, lane A restores. Before the fix, A applies and
DROPS B's entry (B's untracked file appears in A's worktree) and B's stash is
gone from the register. The test asserts the opposite of all three points, plus
that the shared register itself was not modified.

:func:`test_premise_the_raw_hazard_still_reproduces` is the CONTROL: it drives the
same two worktrees with BARE `git stash push`/`pop` and asserts the incident
still happens. If a future git made the stash worktree-scoped, that test fails
and tells us the premise (and this tool's reason to exist) has changed — the
hazard must not be asserted only against our own wrapper.

Run:
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_lane_stash.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "lane_stash.py"
PYTHON = sys.executable


def _load_tool():
    """Import `tools/lane_stash.py` in-process (for the paths no subprocess can force)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("lane_stash_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _env() -> dict[str, str]:
    """A hermetic git environment.

    Every ``GIT_*`` variable is dropped and the global/system config is
    /dev/null'd: a host with ``commit.gpgsign = true`` (or an inherited
    ``GIT_DIR``) would otherwise make a real ``git stash push`` fail or write
    into a peer checkout — the very class of accident these tests model.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "lane-stash test"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "lane-stash@test.invalid"
    return env


class Repo:
    """A temp repo with two linked worktrees, `a` and `b`, on their own branches."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "repo"
        self.a = tmp_path / "wtA"
        self.b = tmp_path / "wtB"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main", str(self.root))
        self.git("config", "user.name", "lane-stash test")
        self.git("config", "user.email", "lane-stash@test.invalid")
        (self.root / "f.txt").write_text("base\n")
        self.git("add", "f.txt")
        self.git("commit", "-qm", "base")
        self.git("worktree", "add", "-q", "-b", "feat/a", str(self.a))
        self.git("worktree", "add", "-q", "-b", "feat/b", str(self.b))

    def git(self, *args: str, cwd: Path | None = None, check: bool = True):
        cp = subprocess.run(
            ["git", *args], cwd=str(cwd or self.root), capture_output=True,
            text=True, env=_env(),
        )
        if check and cp.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {cp.stderr or cp.stdout}")
        return cp

    def lane(self, cwd: Path, *args: str):
        """Invoke tools/lane_stash.py inside `cwd` (a real worktree)."""
        return subprocess.run(
            [PYTHON, str(TOOL), *args], cwd=str(cwd), capture_output=True,
            text=True, errors="replace", env=_env(),
        )

    def git_bytes(self, *args: bytes, cwd: Path | None = None, check: bool = True):
        """Run git with a BYTES argv — for a genuinely non-UTF-8 stash message."""
        cp = subprocess.run(
            [b"git", *args], cwd=str(cwd or self.root), capture_output=True,
            env=_env(),
        )
        if check and cp.returncode != 0:
            raise AssertionError(f"git failed: {cp.stderr!r}")
        return cp

    def status(self, cwd: Path) -> str:
        return self.git("status", "--porcelain", cwd=cwd).stdout

    def stash_shas(self) -> list[str]:
        """The shared register's shas, newest first (position order)."""
        out = self.git("stash", "list", "--format=%H").stdout
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def stash_sha(self) -> str | None:
        cp = self.git("rev-parse", "--verify", "-q", "refs/stash", check=False)
        return cp.stdout.strip() or None

    def lane_refs(self) -> dict[str, str]:
        out = self.git(
            "for-each-ref", "--format=%(refname) %(objectname)", "refs/lane"
        ).stdout
        refs = {}
        for line in out.splitlines():
            ref, _, sha = line.partition(" ")
            refs[ref] = sha
        return refs

    def stash_pins(self) -> dict[str, str]:
        """Only the PIN refs (`refs/lane/<key>/stash/<n>`) — not applied records."""
        return {r: s for r, s in self.lane_refs().items() if "/stash/" in r}

    def applied_refs(self) -> dict[str, str]:
        """Only the durable APPLIED records (`refs/lane/<key>/applied/<sha>`)."""
        return {r: s for r, s in self.lane_refs().items() if "/applied/" in r}

    def dirty(self, cwd: Path, name: str, text: str, untracked: bool = False) -> None:
        (cwd / name).write_text(text)
        if not untracked:
            self.git("add", name, cwd=cwd)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def _fail(cp) -> str:
    return f"rc={cp.returncode}\nstdout:\n{cp.stdout}\nstderr:\n{cp.stderr}"


# ── the acceptance evidence ────────────────────────────────────────────────

def test_acceptance_lane_a_never_applies_lane_bs_entry(repo):
    """A pushes, B pushes, A pops: A gets ONLY A's work, B's entry survives."""
    # lane A: tracked + untracked change, stashed through the tool
    repo.dirty(repo.a, "f.txt", "base\na-from-A\n")
    (repo.a / "uA.txt").write_text("A untracked\n")
    push_a = repo.lane(repo.a, "push", "-u", "-m", "lane A work")
    assert push_a.returncode == 0, _fail(push_a)
    sha_a = repo.stash_sha()
    assert sha_a
    assert repo.status(repo.a) == "", "a lane push must leave the worktree clean"

    # lane B does the same; its push is what renumbers A's entry
    repo.dirty(repo.b, "f.txt", "base\nb-from-B\n")
    (repo.b / "uB.txt").write_text("B untracked\n")
    push_b = repo.lane(repo.b, "push", "-u", "-m", "lane B work")
    assert push_b.returncode == 0, _fail(push_b)
    sha_b = repo.stash_sha()
    assert sha_b and sha_b != sha_a
    assert repo.stash_shas() == [sha_b, sha_a], (
        "the register is repo-global: B's entry is now @{0} and A's renumbered to @{1}"
    )

    # the detector names the hazard in A — a bare pop THERE would take B's work
    check = repo.lane(repo.a, "check")
    assert check.returncode == 1, _fail(check)
    assert "feat/b" in check.stdout

    # lane A restores ITS OWN entry, by lane-scoped sha, with B's entry on top
    pop_a = repo.lane(repo.a, "pop")
    assert pop_a.returncode == 0, _fail(pop_a)

    status = repo.status(repo.a)
    assert " M f.txt" in status and "?? uA.txt" in status, status
    assert "uB.txt" not in status, f"lane A took lane B's work:\n{status}"
    assert not (repo.a / "uB.txt").exists()
    assert "a-from-A" in (repo.a / "f.txt").read_text()

    # B's entry is STILL in the shared register, at its original sha
    assert repo.stash_shas() == [sha_b, sha_a], "lane A dropped or moved a peer's entry"

    # and B can still restore its own work
    pop_b = repo.lane(repo.b, "pop")
    assert pop_b.returncode == 0, _fail(pop_b)
    status_b = repo.status(repo.b)
    assert "?? uB.txt" in status_b and "uA.txt" not in status_b, status_b
    assert "b-from-B" in (repo.b / "f.txt").read_text()


def test_premise_the_raw_hazard_still_reproduces(repo):
    """CONTROL: bare git still steals the peer's entry — the premise of #8051.

    If this ever stops reproducing, git has made the stash worktree-scoped and
    `tools/lane_stash.py` should be re-evaluated rather than silently kept.
    """
    repo.dirty(repo.a, "f.txt", "base\na-from-A\n")
    repo.git("stash", "push", "-q", "-m", "A bare", cwd=repo.a)
    sha_a = repo.stash_sha()

    repo.dirty(repo.b, "f.txt", "base\nb-from-B\n")
    (repo.b / "uB.txt").write_text("B untracked\n")
    repo.git("stash", "push", "-q", "-u", "-m", "B bare", cwd=repo.b)
    sha_b = repo.stash_sha()
    assert sha_b != sha_a

    # A's unqualified pop takes B's entry and drops it
    repo.git("stash", "pop", cwd=repo.a)
    status = repo.status(repo.a)
    assert "?? uB.txt" in status, "the raw hazard no longer reproduces — re-evaluate the tool"
    assert (repo.a / "uB.txt").exists()
    assert repo.stash_shas() == [sha_a], "the bare pop dropped the peer's entry (the incident)"


# ── the detector ───────────────────────────────────────────────────────────

def test_check_is_safe_when_the_top_entry_is_this_branch(repo):
    repo.dirty(repo.a, "f.txt", "base\na\n")
    assert repo.lane(repo.a, "push", "-m", "A").returncode == 0
    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "SAFE" in check.stdout


def test_check_is_safe_on_an_empty_register(repo):
    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "empty" in check.stdout


def test_check_fails_closed_on_an_unattributable_top_entry(repo):
    """A hand-written rescue note (no `On <branch>:` subject) is not 'safe'.

    `git stash push -m` always prefixes `On <branch>:`, but `git stash store -m`
    — how the real register's `RESCUED by <lane>: …` notes were written — does
    not, and an entry whose origin cannot be read must never be reported safe.
    """
    repo.dirty(repo.a, "f.txt", "base\nx\n")
    repo.git("stash", "push", "-q", "-m", "work", cwd=repo.a)
    sha = repo.stash_sha()
    repo.git("stash", "drop", cwd=repo.a)
    repo.git("stash", "store", "-m", "RESCUED by some-other-lane", sha)
    check = repo.lane(repo.a, "check")
    assert check.returncode == 1, _fail(check)
    assert "unreadable subject" in check.stdout


def test_check_fails_closed_when_a_peer_lane_is_on_the_same_branch(repo, tmp_path):
    """Two worktrees can be FORCED onto ONE branch — the lane key must decide.

    `lane_key` is worktree-scoped by design, so two worktrees on one branch are
    two distinct lanes. The branch heuristic cannot tell them apart and reports
    SAFE while a bare `git stash pop` takes the peer's entry (#8051 review P2).
    Pre-fix this fails with exit 0.
    """
    c = tmp_path / "wtC"
    repo.git("worktree", "add", "-q", "--force", str(c), "feat/a")
    for wd in (repo.a, c):
        assert repo.git("rev-parse", "--abbrev-ref", "HEAD", cwd=wd).stdout.strip() == "feat/a"

    repo.dirty(repo.a, "f.txt", "base\na-A\n")
    assert repo.lane(repo.a, "push", "-m", "A work").returncode == 0
    repo.dirty(c, "f.txt", "base\nc-C\n")
    assert repo.lane(c, "push", "-m", "C work").returncode == 0
    shas = repo.stash_shas()
    assert len(shas) == 2 and shas[0] != shas[1]
    assert len(repo.lane_refs()) == 2, repo.lane_refs()  # distinct lanes, one branch

    check = repo.lane(repo.a, "check")
    assert check.returncode == 1, _fail(check)
    assert "SAFE" not in check.stdout, check.stdout

    # the hazard the detector refused to clear is real: a bare pop in A takes C's work
    bare = repo.git("stash", "pop", cwd=repo.a, check=False)
    assert bare.returncode == 0, _fail(bare)
    assert "c-C" in (repo.a / "f.txt").read_text(), (
        "the state must actually be unsafe for the fail-closed assertion to mean anything"
    )


def test_check_is_safe_for_a_recovered_entry_pinned_to_this_lane(repo):
    """A `recover -m` entry carries no lane marker but the pin makes it ours.

    Without checking the lane's own pinned shas, the unreadable subject would
    make `check` report a false HAZARD on work this lane explicitly recovered.
    """
    repo.dirty(repo.a, "f.txt", "base\nrescued\n")
    repo.git("stash", "push", "-q", "-m", "On feat/a: note", cwd=repo.a)
    dropped = repo.stash_sha()
    repo.git("stash", "drop", cwd=repo.a)
    cp = repo.lane(repo.a, "recover", "-m", "rescued note", dropped)
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_shas() == [dropped]

    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "SAFE" in check.stdout


def test_check_fails_closed_on_a_markerless_entry_naming_this_branch(repo, tmp_path):
    """A bare-git entry on THIS branch is not evidence of ownership.

    The register predates this tool, so it is full of marker-less bare-git
    entries whose subjects always name a branch, and two worktrees can be forced
    onto ONE branch. `On feat/a:` names the BRANCH, never the lane, so the
    branch-only fallthrough reported SAFE while a bare `git stash pop` took the
    peer's entry (#8051 review P2). Pre-fix this exits 0.
    """
    c = tmp_path / "wtC"
    repo.git("worktree", "add", "-q", "--force", str(c), "feat/a")
    # a BARE `git stash push` in the peer worktree: no lane marker, no lane pin
    repo.dirty(c, "f.txt", "base\nmarkerless-peer\n")
    repo.git("stash", "push", "-q", "-m", "peer unpinned work", cwd=c)
    assert repo.stash_shas(), "precondition: the peer's entry is in the register"
    assert repo.lane_refs() == {}, "precondition: neither lane pinned anything"
    assert "On feat/a:" in repo.git("stash", "list", "--format=%gs").stdout

    check = repo.lane(repo.a, "check")
    assert check.returncode == 1, _fail(check)
    assert "SAFE" not in check.stdout, check.stdout

    # and the state the detector refused to clear is genuinely unsafe
    bare = repo.git("stash", "pop", cwd=repo.a, check=False)
    assert bare.returncode == 0, _fail(bare)
    assert "markerless-peer" in (repo.a / "f.txt").read_text()


def test_check_json_exposes_the_verdict(repo):
    import json

    repo.dirty(repo.a, "f.txt", "base\na\n")
    repo.lane(repo.a, "push", "-m", "A")
    repo.dirty(repo.b, "f.txt", "base\nb\n")
    repo.lane(repo.b, "push", "-m", "B")
    cp = repo.lane(repo.a, "check", "--json")
    assert cp.returncode == 1, _fail(cp)
    verdict = json.loads(cp.stdout)
    assert verdict["safe"] is False
    assert verdict["bare_pop_branch"] == "feat/b"
    assert verdict["branch"] == "feat/a"
    assert verdict["lane"] != json.loads(repo.lane(repo.b, "check", "--json").stdout)["lane"]


# ── lane scoping ───────────────────────────────────────────────────────────

def test_lane_keys_are_worktree_scoped(repo):
    repo.dirty(repo.a, "f.txt", "base\na\n")
    repo.lane(repo.a, "push", "-m", "A")
    assert repo.lane(repo.b, "list").stdout.count("(no pinned entries)") == 1, (
        "lane B must not see lane A's pinned entry"
    )
    refs = repo.lane_refs()
    assert len(refs) == 1
    assert "/wtA-" in next(iter(refs)), refs


def test_same_basename_worktrees_never_share_a_lane_key(tmp_path):
    """Two worktrees with the SAME directory basename must not collide.

    The ``sha1(resolved root)[:8]`` digest in :func:`lane_key` is load-bearing
    for exactly this shape: ``p1/wt`` and ``p2/wt`` sanitize to the same slug
    ``wt``, so a basename-only key puts both lanes' pins in ONE ref namespace
    and reintroduces the #8051 collision from the other side — a peer's entry
    reachable through our ref, and a ``pop`` here taking it.

    This is the only detector for that: replacing the digest with
    ``sha1(slug)[:8]`` leaves every other test in this file green, so the
    guarantee was unguarded until this test existed (#8773 review F1).
    """
    repo = Repo(tmp_path)
    p1 = tmp_path / "p1" / "wt"
    p2 = tmp_path / "p2" / "wt"
    p1.parent.mkdir()
    p2.parent.mkdir()
    repo.git("worktree", "add", "-q", "-b", "feat/p1", str(p1))
    repo.git("worktree", "add", "-q", "-b", "feat/p2", str(p2))
    assert p1.name == p2.name == "wt"

    mod = _load_tool()
    key1, key2 = mod.lane_key(p1), mod.lane_key(p2)
    assert key1 != key2, f"same-basename worktrees collided on one lane key: {key1}"
    assert key1.startswith("wt-") and key2.startswith("wt-"), (key1, key2)

    # lane `p1/wt` pins its work — under its OWN ref namespace
    repo.dirty(p1, "f.txt", "base\nfrom-p1\n")
    cp = repo.lane(p1, "push", "-m", "p1 work")
    assert cp.returncode == 0, _fail(cp)
    pins = repo.stash_pins()
    assert len(pins) == 1, pins
    assert f"{mod.REFS_NS}/{key1}/stash/1" in pins, pins

    # lane `p2/wt` sees none of it, and cannot pop it
    listing = repo.lane(p2, "list")
    assert "(no pinned entries)" in listing.stdout, listing.stdout
    popped = repo.lane(p2, "pop")
    assert popped.returncode != 0, _fail(popped)
    assert "from-p1" not in (p2 / "f.txt").read_text()

    # and `p1/wt` still restores its own work through its own ref
    assert repo.lane(p1, "pop").returncode == 0
    assert "from-p1" in (p1 / "f.txt").read_text()


def test_a_branch_switch_does_not_orphan_a_lanes_pins(repo):
    """The key is the WORKTREE, not the branch: a baseline checkout keeps the pins.

    A branch-keyed lane ref namespace would make `list` empty after
    `git checkout other-branch` while the entry sat under the old key — and
    taking a baseline on another branch is exactly how the #2654-style incidents
    start.
    """
    repo.dirty(repo.a, "f.txt", "base\na\n")
    assert repo.lane(repo.a, "push", "-m", "baseline work").returncode == 0
    before = repo.lane_refs()

    repo.git("checkout", "-q", "-b", "baseline-check", cwd=repo.a)
    listing = repo.lane(repo.a, "list")
    assert listing.returncode == 0, _fail(listing)
    assert "(no pinned entries)" not in listing.stdout, listing.stdout
    assert repo.lane_refs() == before, "a checkout must not move the lane ref"

    # and the entry is still restorable from the new branch
    assert repo.lane(repo.a, "pop").returncode == 0
    assert "a" in (repo.a / "f.txt").read_text()


def test_lane_ids_are_monotonic_across_pushes(repo):
    for i in range(2):
        repo.dirty(repo.a, "f.txt", f"base\na{i}\n")
        cp = repo.lane(repo.a, "push", "-m", f"push {i}")
        assert cp.returncode == 0, _fail(cp)
    ids = sorted(r.rsplit("/", 1)[-1] for r in repo.lane_refs())
    assert ids == ["1", "2"]


def test_push_with_a_clean_worktree_pins_nothing(repo):
    cp = repo.lane(repo.a, "push", "-m", "nothing here")
    assert cp.returncode == 0, _fail(cp)
    assert "nothing to stash" in cp.stdout
    assert repo.lane_refs() == {}
    assert repo.stash_sha() is None


# ── the contract has no lane override ──────────────────────────────────────

def test_there_is_no_lane_or_ref_override(repo):
    """A peer's lane key is a computable sha1(path)[:8] — naming it must not be possible."""
    for flag in ("--lane", "--ref"):
        cp = repo.lane(repo.a, "list", flag, "someone-elses-lane")
        assert cp.returncode != 0
        assert "unrecognized arguments" in cp.stderr


# ── failure modes ──────────────────────────────────────────────────────────

def test_pop_that_conflicts_keeps_the_pin_and_the_work(repo):
    repo.dirty(repo.a, "f.txt", "base\nstashed-A\n")
    assert repo.lane(repo.a, "push", "-m", "A").returncode == 0

    # a divergent local edit to the same line makes the apply conflict
    (repo.a / "f.txt").write_text("base\nlocal-divergent\n")
    cp = repo.lane(repo.a, "pop")
    assert cp.returncode == 3, _fail(cp)
    assert "entry kept" in cp.stderr, cp.stderr
    assert len(repo.lane_refs()) == 1, "a conflicted apply must not release the pin"

    # the pin is the only handle on that work: recover by staging away the conflict
    repo.git("checkout", "--", "f.txt", cwd=repo.a)
    retry = repo.lane(repo.a, "pop")
    assert retry.returncode == 0, _fail(retry)
    assert "stashed-A" in (repo.a / "f.txt").read_text()
    assert repo.stash_pins() == {}


def test_drop_requires_yes(repo):
    repo.dirty(repo.a, "f.txt", "base\na\n")
    repo.lane(repo.a, "push", "-m", "A")
    cp = repo.lane(repo.a, "drop", "1")
    assert cp.returncode == 2, _fail(cp)
    assert "without --yes" in cp.stderr
    assert len(repo.lane_refs()) == 1
    assert repo.lane(repo.a, "drop", "1", "--yes").returncode == 0
    assert repo.lane_refs() == {}


def test_pop_unknown_id_is_an_error(repo):
    repo.dirty(repo.a, "f.txt", "base\na\n")
    repo.lane(repo.a, "push", "-m", "A")
    cp = repo.lane(repo.a, "pop", "7")
    assert cp.returncode == 2, _fail(cp)
    assert "no entry 7" in cp.stderr


# ── recovery + reclamation ─────────────────────────────────────────────────

def test_recover_restores_a_dropped_entry_and_pins_it(repo):
    repo.dirty(repo.a, "f.txt", "base\npeer-work\n")
    (repo.a / "peer.txt").write_text("peer untracked\n")
    repo.git("stash", "push", "-q", "-u", "-m", "On feat/a: peer rescue note", cwd=repo.a)
    dropped = repo.stash_sha()
    repo.git("stash", "drop", cwd=repo.a)
    assert repo.stash_shas() == []

    cp = repo.lane(repo.a, "recover", dropped)
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_shas() == [dropped], "the recovered entry must be back in the register"
    assert len(repo.lane_refs()) == 1, "a recovered entry must be pinned this time"

    # and it is restorable by the lane it was recovered into
    assert repo.lane(repo.a, "pop").returncode == 0
    assert "peer-work" in (repo.a / "f.txt").read_text()


def test_recover_refuses_a_non_stash_commit(repo):
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    cp = repo.lane(repo.a, "recover", head)
    assert cp.returncode == 2, _fail(cp)
    assert "not a stash entry" in cp.stderr
    assert repo.stash_shas() == []


def test_prune_refuses_unpinned_entries_and_removes_pinned_ones(repo):
    # a bare-git lane's entry (pinned nowhere) + a pinned lane entry
    repo.dirty(repo.a, "f.txt", "base\nbare\n")
    repo.git("stash", "push", "-q", "-m", "bare-git work", cwd=repo.a)
    bare_sha = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\npinned\n")
    assert repo.lane(repo.b, "push", "-m", "pinned work").returncode == 0
    pinned_sha = repo.stash_sha()
    assert repo.stash_shas() == [pinned_sha, bare_sha]

    dry = repo.lane(repo.a, "prune")
    assert dry.returncode == 0, _fail(dry)
    assert "refused" in dry.stdout and "dry run" in dry.stdout
    assert repo.stash_shas() == [pinned_sha, bare_sha], "a dry run must not delete anything"

    applied = repo.lane(repo.a, "prune", "--apply")
    assert applied.returncode == 0, _fail(applied)
    assert repo.stash_shas() == [bare_sha], "only the pinned entry may be reclaimed"
    assert pinned_sha not in repo.stash_shas()

    # the object is still reachable and the lane can still restore its work
    refs = repo.lane_refs()
    assert refs and pinned_sha in set(refs.values())
    assert repo.lane(repo.b, "pop").returncode == 0
    assert "pinned" in (repo.b / "f.txt").read_text()


def test_prune_of_the_last_entry_drops_refs_stash_and_unwedges_check(repo):
    """Reclaiming the LAST entry must drop `refs/stash`, not only its reflog.

    `git reflog delete --updateref` empties the reflog but git does NOT drop the
    ref when it empties (measured — the plan's row 8 asserted the opposite), so
    the "removed" entry stays live for bare git (`git stash pop` applies AND
    drops it) and `check` fails closed in every worktree with no lane_stash
    command able to clear it (#8051 review P2). Pre-fix: `refs/stash` still
    resolves and `check` exits 1.
    """
    repo.dirty(repo.a, "f.txt", "base\na\n")
    assert repo.lane(repo.a, "push", "-m", "A").returncode == 0
    assert repo.stash_sha()

    cp = repo.lane(repo.a, "prune", "--apply")
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_shas() == [], "the reflog must be empty"
    assert repo.stash_sha() is None, (
        "refs/stash must be dropped with the last entry, as `git stash drop` does — "
        "otherwise the reclaimed entry is still live for bare git"
    )

    # the detector the guidance tells lanes to trust must no longer be wedged
    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "SAFE" in check.stdout

    bare = repo.git("stash", "pop", cwd=repo.a, check=False)
    assert bare.returncode != 0, f"the reclaimed entry is still poppable:\n{_fail(bare)}"


def test_prune_clears_an_already_orphaned_refs_stash(repo):
    """A register emptied by an EARLIER prune can still be cleared by prune.

    `reflog delete --updateref` on the last entry leaves `refs/stash` resolving
    while the register enumerates nothing; before the fix that state made
    `check` exit 1 in every worktree and no lane_stash command could clear it
    (the register read returned 0 rows and prune returned early).
    """
    repo.dirty(repo.a, "f.txt", "base\na\n")
    assert repo.lane(repo.a, "push", "-m", "A").returncode == 0
    # reproduce the wedge the pre-fix prune left behind
    repo.git("reflog", "delete", "--updateref", "refs/stash@{0}")
    assert repo.stash_shas() == [] and repo.stash_sha() is not None
    assert repo.lane(repo.a, "check").returncode == 1

    assert repo.lane(repo.a, "prune").returncode == 0  # dry run, no-op or reported
    cp = repo.lane(repo.a, "prune", "--apply")
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_sha() is None, "prune must clear the orphaned refs/stash"
    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "SAFE" in check.stdout


def test_prune_refuses_an_unpinned_refs_stash_orphan(repo):
    """An emptied reflog over a PEER's bare entry must survive `prune --apply`.

    `git reflog expire` (routine maintenance) leaves `refs/stash` pointing at an
    entry no lane ref pins. That ref is the peer's ONLY handle — dropping it makes
    the commit gc-eligible — and the command's own line above it reports the entry
    as refused. Pre-fix `prune --apply` dropped the ref anyway, destroying a
    peer's stash with no lane_stash recovery path (#8051 review P1).
    """
    # the peer's BARE stash: marker-less, pinned by no lane ref
    repo.dirty(repo.b, "f.txt", "base\npeer-only\n")
    repo.git("stash", "push", "-q", "-m", "peer unpinned work", cwd=repo.b)
    sha = repo.stash_sha()
    assert sha and repo.lane_refs() == {}, "precondition: unpinned peer entry"

    # routine maintenance empties the reflog; the ref (and the peer's handle) live
    repo.git("reflog", "expire", "--expire=now", "--all")
    assert repo.stash_shas() == [] and repo.stash_sha() == sha

    cp = repo.lane(repo.a, "prune", "--apply")
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_sha() == sha, (
        "prune destroyed an unpinned peer's last handle — the object is now gc-eligible"
    )
    assert "recover" in cp.stdout, cp.stdout

    # the peer can still restore its own work
    bare = repo.git("stash", "pop", cwd=repo.b, check=False)
    assert bare.returncode == 0, _fail(bare)
    assert "peer-only" in (repo.b / "f.txt").read_text()


def test_prune_refuses_a_foreign_lanes_unpinned_entry(repo):
    """Only THIS lane's own marker is reclaimable — a peer's popped entry is kept.

    `pop` leaves the shared entry behind, so another lane's `prune` sees a
    marker-less-from-its-perspective (foreign) unpinned entry. Reclaiming it would
    be as lossy as dropping an orphan (#8051 review P1).
    """
    repo.dirty(repo.b, "f.txt", "base\nb-work\n")
    assert repo.lane(repo.b, "push", "-m", "B").returncode == 0
    assert repo.lane(repo.b, "pop").returncode == 0
    sha = repo.stash_sha()
    assert sha and repo.stash_pins() == {}, "precondition: B's entry is unpinned"
    assert repo.applied_refs(), "precondition: B left a durable applied-record"

    cp = repo.lane(repo.a, "prune", "--apply")
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_sha() == sha, "prune dropped a peer lane's entry"
    assert repo.stash_shas() == [sha]
    assert repo.applied_refs(), "A's prune must not touch B's applied-record"


def test_prune_reclaims_this_lanes_own_popped_entries(repo):
    """`push` then `pop` leaves an unpinned entry; prune must reclaim this lane's own.

    `pop` deliberately leaves the shared entry in place and releases the lane pin
    (see the module's OVERRIDES note), so the register grows with every push/pop
    cycle. Pre-fix `prune` refused all of it as "unpinned", leaving no lane_stash
    command able to reclaim the tool's own entries (#8051 review P2).
    """
    for i in range(3):
        repo.dirty(repo.a, "f.txt", f"base\na{i}\n")
        assert repo.lane(repo.a, "push", "-m", f"cycle {i}").returncode == 0
        assert repo.lane(repo.a, "pop").returncode == 0
    assert len(repo.stash_shas()) == 3, repo.stash_shas()
    assert repo.stash_pins() == {}, "precondition: pop released every lane pin"
    assert len(repo.applied_refs()) == 3, repo.applied_refs()

    cp = repo.lane(repo.a, "prune", "--apply")
    assert cp.returncode == 0, _fail(cp)
    assert repo.stash_shas() == [], "this lane's own popped entries were not reclaimed"
    assert repo.stash_sha() is None
    assert repo.applied_refs() == {}, "applied-records are dropped with the entries they named"
    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "SAFE" in check.stdout


def test_pop_never_modifies_the_shared_register(repo):
    """`pop` is deliberately not `git stash pop` (see the module's OVERRIDES note)."""
    repo.dirty(repo.a, "f.txt", "base\na\n")
    repo.lane(repo.a, "push", "-m", "A")
    before = repo.stash_shas()
    assert before
    assert repo.lane(repo.a, "pop").returncode == 0
    assert repo.stash_shas() == before, (
        "the shared entry must survive `pop`: its positional reflog delete is racy and "
        "can drop a bare-git peer's unpinned entry"
    )
    assert repo.stash_pins() == {}, "the lane's own pin is released"
    assert repo.applied_refs(), (
        "pop must leave durable evidence it applied the entry — the message marker is "
        "written before `_pin`, so it is not evidence of application"
    )


# ── fail-closed detection when the register cannot be read (#8051 review P1) ──

def test_check_fails_closed_when_refs_stash_is_not_in_the_reflog(repo):
    """`refs/stash` resolving but `git stash list` empty is UNSAFE, not SAFE.

    Reflog expiry is ordinary git maintenance. It can remove every reflog entry
    while `refs/stash` still points at a real (here: FOREIGN) stash, and then a
    bare `git stash pop` applies AND drops that entry. The detector must not
    answer "safe" from the absence of rows (#8051 review P1).
    """
    # lane B's entry becomes the shared register's top entry
    repo.dirty(repo.b, "f.txt", "base\nb-from-B\n")
    (repo.b / "uB.txt").write_text("B untracked\n")
    assert repo.lane(repo.b, "push", "-u", "-m", "lane B work").returncode == 0
    sha_b = repo.stash_sha()
    assert sha_b

    # routine maintenance: the reflog is gone, but the ref (and B's entry) live
    repo.git("reflog", "expire", "--expire=now", "--all")
    assert repo.stash_shas() == [], "precondition: the reflog must be empty after expiry"
    assert repo.stash_sha() == sha_b, "precondition: refs/stash must still resolve"

    # from lane A the detector must NOT clear a bare `git stash pop`
    check = repo.lane(repo.a, "check")
    assert check.returncode != 0, _fail(check)
    assert "SAFE" not in check.stdout, check.stdout

    # and the hazard the detector just refused to clear is real: bare pop takes B
    bare = repo.git("stash", "pop", cwd=repo.a, check=False)
    assert bare.returncode == 0, _fail(bare)
    assert (repo.a / "uB.txt").exists(), (
        "the state must actually be unsafe for the fail-closed assertion to mean anything"
    )
    assert repo.stash_sha() is None, "the bare pop dropped B's entry (the incident)"


def test_check_read_failure_is_a_state_error(repo, monkeypatch):
    """An unreadable `git stash list` exits non-zero, never `SAFE` (exit 0)."""
    mod = _load_tool()
    monkeypatch.chdir(repo.a)
    real_git = mod._git

    def fake_git(*args, check=True):
        if args[:2] == ("stash", "list"):
            return subprocess.CompletedProcess(
                ["git", *args], 1, "", "fatal: cannot read refs/stash"
            )
        return real_git(*args, check=check)

    monkeypatch.setattr(mod, "_git", fake_git)
    rc = mod.main(["check", "--json"])
    assert rc == 2, f"an unreadable register is a state error (exit 2), got {rc}"


# ── a valid lane ref for a git-hostile worktree name (#8051 review P2) ──

def test_lane_key_is_a_valid_git_ref_for_hostile_directory_names(tmp_path):
    """`lane_key` must emit a git-VALID ref component, not merely a safe filename.

    `..`, a leading/trailing `.`, and a trailing `.lock` are all legal in a
    directory basename and all rejected by `git check-ref-format`. Left in the
    slug they make `_pin` fail after the stash already exists (#8051 review P2).
    """
    mod = _load_tool()
    for name in ("a..b", "lane.lock", "x.lock.lock", ".hidden", "trailing.", "weird@{}name"):
        d = tmp_path / name
        d.mkdir()
        key = mod.lane_key(d)
        ref = f"{mod.REFS_NS}/{key}/stash/1"
        cp = subprocess.run(["git", "check-ref-format", ref], capture_output=True, text=True)
        assert cp.returncode == 0, f"{name!r} -> {ref!r} is not a valid ref: {cp.stderr}"


def test_push_pins_in_a_hostile_named_worktree(repo, tmp_path):
    """End to end: a `a..b.lock` worktree can push and its pin is a valid ref."""
    hostile = tmp_path / "a..b.lock"
    repo.git("worktree", "add", "-q", "-b", "feat/hostile", str(hostile))
    repo.dirty(hostile, "f.txt", "base\nhostile-work\n")
    cp = repo.lane(hostile, "push", "-m", "hostile lane")
    assert cp.returncode == 0, _fail(cp)
    refs = repo.lane_refs()
    assert len(refs) == 1, refs
    ref = next(iter(refs))
    assert subprocess.run(["git", "check-ref-format", ref]).returncode == 0, ref
    # the pin is real: the lane can restore its own work
    assert repo.lane(hostile, "pop").returncode == 0
    assert "hostile-work" in (hostile / "f.txt").read_text()


def test_push_reports_the_orphan_when_pinning_fails(repo, monkeypatch, capsys):
    """A `_pin` failure must say the stash EXISTS and how to recover it.

    The stash commit is already in the shared register by the time `_pin` runs;
    an error that reads like "nothing was created" hides an unpinned entry a
    bare `git stash pop` can take (#8051 review P2).
    """
    mod = _load_tool()
    monkeypatch.chdir(repo.a)
    repo.dirty(repo.a, "f.txt", "base\norphan-me\n")

    def boom(key, sha):
        raise mod.LaneStashError("could not allocate a lane ref under refs/lane/x")

    monkeypatch.setattr(mod, "_pin", boom)
    rc = mod.main(["push", "-m", "W"])
    assert rc == 2, rc
    err = capsys.readouterr().err
    assert "EXISTS" in err and "recover" in err, err
    assert repo.stash_sha() is not None, "the entry must actually be in the register"


def test_prune_refuses_a_never_applied_pin_failure_entry(repo, monkeypatch):
    """A pin-failure entry is own-MARKED, unpinned and NEVER APPLIED — prune keeps it.

    `cmd_push` writes the lane marker into the stash message BEFORE `_pin` runs,
    so the marker is an ownership hint, not proof of application. `prune`'s
    unpinned branch used `_subject_lane_key(r["subject"]) == key` as a proxy for
    "already restored" — false for a pin-failure entry, whose ONLY handle is the
    shared-register entry. Removing it deletes the reflog entry and drops
    `refs/stash`, destroying that work for good (#8773 review P1). Pre-fix this
    test fails with the entry gone.
    """
    mod = _load_tool()
    monkeypatch.chdir(repo.a)
    repo.dirty(repo.a, "f.txt", "base\nnever-applied\n")

    def boom(key, sha):
        raise mod.LaneStashError("could not allocate a lane ref under refs/lane/x")

    monkeypatch.setattr(mod, "_pin", boom)
    assert mod.main(["push", "-m", "W"]) == 2
    assert repo.stash_sha() is not None

    # precondition: own-MARKED, but unpinned and with no applied-record
    sha = repo.stash_sha()
    assert repo.lane_refs() == {}, "precondition: the pin failed, so nothing is pinned"
    assert mod._subject_lane_key(repo.git("log", "-1", "--format=%s", sha).stdout.strip()) \
        == mod.lane_key(), "precondition: the entry carries THIS lane's marker"
    assert sha not in mod._applied_shas(mod.lane_key()), "precondition: never applied"

    # the marker must NOT be read as evidence that the work was restored
    assert mod.main(["prune", "--apply"]) == 0
    assert repo.stash_sha() == sha, (
        "prune destroyed the only handle to a never-applied pin-failure entry"
    )
    assert repo.stash_shas() == [sha]

    # and the work is still there for the lane that created it
    bare = repo.git("stash", "pop", cwd=repo.a, check=False)
    assert bare.returncode == 0, _fail(bare)
    assert "never-applied" in (repo.a / "f.txt").read_text()


def test_prune_refuses_a_never_applied_pin_failure_orphan(repo, monkeypatch):
    """The ORPHAN gate must also require the applied-record, not the marker.

    An own-marked, unpinned, never-applied pin-failure entry that is then
    orphaned by routine reflog expiry (`refs/stash` resolves while the register
    enumerates nothing) must survive `prune --apply`: its only handle is the
    ref, and the marker is not proof the work was restored. This pins the
    `_orphan_is_reclaimable` half of the fix — reverting it to the marker
    logic leaves the rest of the suite green (#8773 review P2).
    """
    mod = _load_tool()
    monkeypatch.chdir(repo.a)
    repo.dirty(repo.a, "f.txt", "base\norphaned-never-applied\n")

    def boom(key, sha):
        raise mod.LaneStashError("could not allocate a lane ref under refs/lane/x")

    monkeypatch.setattr(mod, "_pin", boom)
    assert mod.main(["push", "-m", "W"]) == 2
    sha = repo.stash_sha()
    assert sha

    # routine maintenance empties the reflog; the ref (and the work) live on
    repo.git("reflog", "expire", "--expire=now", "--all")
    assert repo.stash_shas() == [], "precondition: the register enumerates nothing"
    assert repo.stash_sha() == sha, "precondition: refs/stash still resolves"
    assert sha not in mod._applied_shas(mod.lane_key()), "precondition: never applied"

    assert mod.main(["prune", "--apply"]) == 0
    assert repo.stash_sha() == sha, (
        "prune dropped an orphaned never-applied entry's only handle "
        "(the marker was read as proof of application)"
    )


def test_prune_cannot_lose_a_kept_entry_to_a_concurrent_push(repo, monkeypatch):
    """A peer push between prune's verify and its positional delete must not lose work.

    `git reflog delete --updateref refs/stash@{i}` re-resolves the position at
    DELETE time, so a push landing after the verification shifts every position
    and the delete removes a KEPT entry — a peer's unpinned one, whose only
    handle was the reflog. `prune` guard-pins every kept object and re-stores
    anything the shift removed (#8773 review P1). Pre-fix this loses the peer's
    entry.
    """
    mod = _load_tool()
    # lane A's PINNED entry (doomed) and a peer's bare, unpinned entry (kept)
    repo.dirty(repo.a, "f.txt", "base\na-pinned\n")
    assert repo.lane(repo.a, "push", "-m", "A pinned").returncode == 0
    pinned = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\npeer-bare\n")
    repo.git("stash", "push", "-q", "-m", "peer bare work", cwd=repo.b)
    peer = repo.stash_sha()
    assert peer and peer != pinned
    assert repo.stash_shas() == [peer, pinned]
    # precondition: the peer entry is unpinned, A's is pinned
    pinned_shas = set(repo.stash_pins().values())
    assert pinned in pinned_shas and peer not in pinned_shas, (pinned_shas, repo.stash_pins())

    monkeypatch.chdir(repo.a)
    real_git = mod._git
    injected = {"done": False}

    def wrapping_git(*args, check=True, stdin=None):
        # A peer push in the verify -> delete window: the exact race the guard
        # and reconciliation exist for.
        if args[:2] == ("reflog", "delete") and not injected["done"]:
            injected["done"] = True
            repo.dirty(repo.b, "f.txt", "base\nlate-push\n")
            repo.git("stash", "push", "-q", "-m", "late peer push", cwd=repo.b)
        return real_git(*args, check=check, stdin=stdin)

    monkeypatch.setattr(mod, "_git", wrapping_git)
    assert mod.main(["prune", "--apply"]) == 0
    assert injected["done"], "the test never exercised the shift"
    assert peer in repo.stash_shas(), (
        "prune lost the peer's only handle to a positional shift — the guard/reconcile failed"
    )
    # the peer can still restore its work
    bare = repo.git("stash", "pop", cwd=repo.b, check=False)
    assert bare.returncode == 0, _fail(bare)
    assert "peer-bare" in (repo.b / "f.txt").read_text()


def _four_entry_register(repo):
    """K0(bare) D1(pinned) K2(bare) D3(pinned), newest first: [D3, K2, D1, K0].

    Both pinned entries are doomed and occupy positions 0 and 2, so the prune's
    high->low loop deletes @{2} first and then finds @{0} shifted by a push.
    """
    repo.dirty(repo.b, "f.txt", "base\nk0\n")
    repo.git("stash", "push", "-q", "-m", "k0", cwd=repo.b)
    k0 = repo.stash_sha()
    repo.dirty(repo.a, "f.txt", "base\nd1\n")
    assert repo.lane(repo.a, "push", "-m", "d1").returncode == 0
    d1 = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\nk2\n")
    repo.git("stash", "push", "-q", "-m", "k2", cwd=repo.b)
    k2 = repo.stash_sha()
    repo.dirty(repo.a, "f.txt", "base\nd3\n")
    assert repo.lane(repo.a, "push", "-m", "d3").returncode == 0
    d3 = repo.stash_sha()
    assert repo.stash_shas() == [d3, k2, d1, k0]
    return k0, d1, k2, d3


def test_prune_restores_a_kept_entry_even_when_the_loop_stops(repo, monkeypatch):
    """Reconciliation must run on the STOP path too, not just a clean finish.

    A push that lands inside a delete removes a kept entry; the next target's
    re-verification then fails and the loop STOPS. Returning early there skipped
    the reconciliation and the `finally` unpinned the guard anyway, destroying
    the only handle to the removed kept entry (#8773 review P1).
    """
    mod = _load_tool()
    _k0, _d1, k2, _d3 = _four_entry_register(repo)
    monkeypatch.chdir(repo.a)
    real_git = mod._git
    injected = {"done": False}

    def wrapping_git(*args, check=True, stdin=None):
        if args[:2] == ("reflog", "delete") and not injected["done"]:
            injected["done"] = True
            repo.dirty(repo.b, "f.txt", "base\nlate-stop\n")
            repo.git("stash", "push", "-q", "-m", "late stop", cwd=repo.b)
        return real_git(*args, check=check, stdin=stdin)

    monkeypatch.setattr(mod, "_git", wrapping_git)
    assert mod.main(["prune", "--apply"]) == 0
    assert injected["done"], "the test never exercised the shift"
    shas = repo.stash_shas()
    assert k2 in shas, "a kept entry removed by a shift was lost on the STOP path"


def test_prune_retains_the_guard_when_it_cannot_restore(repo, monkeypatch):
    """A failed re-store must NOT unpin the guard — it is then the last handle.

    The warning says the guard still holds the object; the `finally` must make
    that true, or the object becomes gc-eligible with no handle left (#8773 P1).
    """
    mod = _load_tool()
    _k0, _d1, k2, _d3 = _four_entry_register(repo)
    monkeypatch.chdir(repo.a)
    real_git = mod._git
    injected = {"pushed": False, "failed": False}

    def wrapping_git(*args, check=True, stdin=None):
        if args[:2] == ("reflog", "delete") and not injected["pushed"]:
            injected["pushed"] = True
            repo.dirty(repo.b, "f.txt", "base\nlate-guard\n")
            repo.git("stash", "push", "-q", "-m", "late guard", cwd=repo.b)
        if args[:2] == ("stash", "store") and not injected["failed"]:
            injected["failed"] = True
            return subprocess.CompletedProcess(
                ["git", *args], 1, "", "injected store failure"
            )
        return real_git(*args, check=check, stdin=stdin)

    monkeypatch.setattr(mod, "_git", wrapping_git)
    assert mod.main(["prune", "--apply"]) == 0
    assert injected["pushed"] and injected["failed"], "the failure path was not exercised"
    guards = repo.git(
        "for-each-ref", "--format=%(objectname)", "refs/pi-lane-prune-guard"
    ).stdout.split()
    assert k2 in guards, (
        "the guard ref was unpinned although the entry was never re-stored — "
        "the object is now gc-eligible with no handle"
    )
    assert repo.git("cat-file", "-e", f"{k2}^{{commit}}", check=False).returncode == 0


def test_prune_warns_when_a_concurrent_push_shifts_the_position(repo, monkeypatch, capsys):
    """A during-prune push removed by a @{0} delete must be REPORTED, not silent.

    The victim was pushed after the prune's snapshot, so it is outside the guard
    set and cannot be restored (its sha is unknown). The delete must notice that
    the intended entry is still registered and say so, and it must not count the
    entry as removed (#8773 review P2).
    """
    mod = _load_tool()
    repo.dirty(repo.a, "f.txt", "base\ndoomed\n")
    assert repo.lane(repo.a, "push", "-m", "doomed").returncode == 0
    doomed = repo.stash_sha()
    assert repo.stash_shas() == [doomed]
    monkeypatch.chdir(repo.a)
    real_git = mod._git
    injected = {"done": False}

    def wrapping_git(*args, check=True, stdin=None):
        if args[:2] == ("reflog", "delete") and not injected["done"]:
            injected["done"] = True
            repo.dirty(repo.b, "f.txt", "base\nlate-warn\n")
            repo.git("stash", "push", "-q", "-m", "late warn", cwd=repo.b)
        return real_git(*args, check=check, stdin=stdin)

    monkeypatch.setattr(mod, "_git", wrapping_git)
    capsys.readouterr()
    assert mod.main(["prune", "--apply"]) == 0
    assert injected["done"]
    out = capsys.readouterr().out
    assert "WARNING" in out, out
    assert doomed in repo.stash_shas(), "the intended entry must still be registered"
    assert "removed 0" in out, out


def test_prune_reports_a_register_changed_by_a_concurrent_remover(repo, monkeypatch, capsys):
    """A concurrent remover plus TWO pushes must be REPORTED, not read as success.

    The intended sha's ABSENCE is not proof our delete took it: another lane's
    `prune --apply` can reclaim the same doomed entry while peer pushes shift
    positions, and the positional delete then removes a during-prune push. When
    the pushes outnumber the concurrent removals the register's sha multiset
    still differs from the expected one, and the whole-state comparison catches
    it (#8773 review P1). The one-push variant cancels out and is the documented
    residual — see `test_prune_cannot_distinguish_a_cancelled_shift`.
    """
    mod = _load_tool()
    repo.dirty(repo.a, "f.txt", "base\ndoomed\n")
    assert repo.lane(repo.a, "push", "-m", "doomed").returncode == 0
    doomed = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\nkept\n")
    repo.git("stash", "push", "-q", "-m", "kept", cwd=repo.b)
    kept = repo.stash_sha()
    assert repo.stash_shas() == [kept, doomed]  # doomed sits at refs/stash@{1}
    monkeypatch.chdir(repo.a)
    real_git = mod._git
    injected = {"done": False}

    def wrapping_git(*args, check=True, stdin=None):
        if args[:2] == ("reflog", "delete") and not injected["done"]:
            injected["done"] = True
            repo.dirty(repo.b, "f.txt", "base\np1\n")
            repo.git("stash", "push", "-q", "-m", "p1", cwd=repo.b)
            repo.dirty(repo.b, "f.txt", "base\np2\n")
            repo.git("stash", "push", "-q", "-m", "p2", cwd=repo.b)
            # a concurrent peer prune reclaims the shared doomed entry
            repo.git("reflog", "delete", "--updateref", "refs/stash@{3}")
        return real_git(*args, check=check, stdin=stdin)

    monkeypatch.setattr(mod, "_git", wrapping_git)
    capsys.readouterr()
    assert mod.main(["prune", "--apply"]) == 0
    assert injected["done"]
    out = capsys.readouterr().out
    assert "WARNING" in out, out
    assert "removed 0" in out, out
    assert kept in repo.stash_shas(), "the kept entry must survive the interleaving"


def test_prune_cannot_distinguish_a_cancelled_shift(repo, monkeypatch, capsys):
    """The documented residual: a cancelled shift is NOT detectable.

    One during-prune push and a concurrent remover reclaiming the intended sha
    cancel out in the register's sha multiset, so `prune` reports success while
    the during-prune entry was the one removed. Git exposes no
    compare-and-delete and the two histories are observationally identical, so
    this is a limit of the primitive, not an unhandled case — the plan's
    "Residual race" note and this test keep it from regressing into an
    overclaim (#8773 review P1).
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\nkept\n")
    repo.git("stash", "push", "-q", "-m", "kept", cwd=repo.b)
    kept = repo.stash_sha()
    repo.dirty(repo.a, "f.txt", "base\ndoomed\n")
    assert repo.lane(repo.a, "push", "-m", "doomed").returncode == 0
    doomed = repo.stash_sha()
    assert repo.stash_shas() == [doomed, kept]  # doomed sits at refs/stash@{0}
    monkeypatch.chdir(repo.a)
    real_git = mod._git
    injected = {"done": False, "p": None}

    def wrapping_git(*args, check=True, stdin=None):
        if args[:2] == ("reflog", "delete") and not injected["done"]:
            injected["done"] = True
            repo.dirty(repo.b, "f.txt", "base\np\n")
            repo.git("stash", "push", "-q", "-m", "p", cwd=repo.b)
            injected["p"] = repo.stash_sha()
            # a concurrent remover reclaims the shared doomed entry
            repo.git("reflog", "delete", "--updateref", "refs/stash@{1}")
        return real_git(*args, check=check, stdin=stdin)

    monkeypatch.setattr(mod, "_git", wrapping_git)
    capsys.readouterr()
    assert mod.main(["prune", "--apply"]) == 0
    assert injected["done"]
    out = capsys.readouterr().out
    # DOCUMENTED RESIDUAL: the register is exactly `expected - {doomed}`, so the
    # whole-state comparison cannot tell this from a clean delete.
    assert "WARNING" not in out, out
    assert "removed 1" in out, out
    assert injected["p"] not in repo.stash_shas(), "the residual victim is gone"
    assert kept in repo.stash_shas(), "the kept entry survives"


def test_prune_orphan_survives_a_reconciliation_store(repo, monkeypatch, capsys):
    """A retained orphan must still be named by a ref after the prune.

    When the register empties during a prune and `refs/stash` points at an
    unpinned orphan, the retention message promises a handle — but `prune`'s
    reconciliation then runs `git stash store` for a guarded kept entry, which
    MOVES `refs/stash` onto it and would leave the orphan handle-less. The
    orphan is guard-pinned instead, and that ref must outlive the store (#8773
    review P2).
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\nkept\n")
    repo.git("stash", "push", "-q", "-m", "kept", cwd=repo.b)
    kept = repo.stash_sha()
    repo.dirty(repo.a, "f.txt", "base\ndoomed\n")
    assert repo.lane(repo.a, "push", "-m", "doomed").returncode == 0
    doomed = repo.stash_sha()
    assert repo.stash_shas() == [doomed, kept]
    monkeypatch.chdir(repo.a)
    real_git = mod._git
    injected = {"done": False, "p": None}

    def wrapping_git(*args, check=True, stdin=None):
        if args[:2] == ("reflog", "delete") and not injected["done"]:
            injected["done"] = True
            repo.dirty(repo.b, "f.txt", "base\np\n")
            repo.git("stash", "push", "-q", "-m", "p", cwd=repo.b)
            injected["p"] = repo.stash_sha()
            # concurrent removals empty the register; refs/stash is left on `p`
            repo.git("reflog", "delete", "--updateref", "refs/stash@{1}")  # doomed
            repo.git("reflog", "delete", "--updateref", "refs/stash@{1}")  # kept
        return real_git(*args, check=check, stdin=stdin)

    monkeypatch.setattr(mod, "_git", wrapping_git)
    capsys.readouterr()
    assert mod.main(["prune", "--apply"]) == 0
    assert injected["done"]
    out = capsys.readouterr().out
    assert "RESTORED" in out, out  # reconciliation stored `kept`
    p = injected["p"]
    refs = repo.git("for-each-ref", "--format=%(refname) %(objectname)").stdout.splitlines()
    assert any(p == ln.split()[-1] for ln in refs), refs
    assert kept in repo.stash_shas(), "the kept entry was restored"


def test_recover_repairs_a_retained_orphan_and_frees_its_guard(repo, monkeypatch, capsys):
    """`recover` must actually create a reflog entry, and release the guard.

    `git stash store` is a SILENT no-op when `refs/stash` already equals the sha
    (the state an emptied reflog leaves), so the retained orphan's own repair
    instruction used to do nothing while printing success and leaving `check`
    fail-closed. The store is now verified, and once the entry is pinned under
    `refs/lane` the prune guard is released (#8773 review P1/P2).
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\norphan\n")
    repo.git("stash", "push", "-q", "-m", "orphan", cwd=repo.b)
    orphan = repo.stash_sha()
    # empty the reflog WITHOUT dropping refs/stash: the hazard state retention
    # exists to protect (a bare `git stash pop` can still take `orphan`)
    repo.git("reflog", "delete", "--updateref", "refs/stash@{0}")
    assert repo.stash_shas() == []
    monkeypatch.chdir(repo.a)
    capsys.readouterr()
    assert mod.main(["prune", "--apply"]) == 0
    out = capsys.readouterr().out
    assert orphan is not None and orphan[:12] in out, out
    assert "recover" in out, out
    guards = repo.git(
        "for-each-ref", "--format=%(refname)", "refs/pi-lane-prune-guard"
    ).stdout.split()
    assert guards, "the retained orphan must be held by a guard ref"

    # follow the retention message's own instruction
    assert mod.main(["recover", orphan, "-m", "recovered orphan"]) == 0
    capsys.readouterr()
    assert repo.stash_shas() == [orphan], "recover must add a real reflog entry"
    assert repo.lane(repo.a, "check").returncode == 0, "the repair must leave check SAFE"
    assert repo.git(
        "for-each-ref", "--format=%(refname)", "refs/pi-lane-prune-guard"
    ).stdout.strip() == "", "the lane pin makes the guard redundant"


def test_prune_handles_duplicate_register_entries(repo, monkeypatch, capsys):
    """A duplicate sha must not turn a correct delete into a false warning.

    `recover <sha>` of a sha already in the register prepends it, giving
    `[E, A, E]`. A set-style comparison drops BOTH occurrences and then reads
    the correct removal of one as a concurrency event; the state comparison is a
    Counter so exactly one occurrence is removed per delete (#8773 review P2).
    """
    mod = _load_tool()
    repo.dirty(repo.a, "f.txt", "base\ndoomed\n")
    assert repo.lane(repo.a, "push", "-m", "doomed").returncode == 0
    doomed = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\nkept\n")
    repo.git("stash", "push", "-q", "-m", "kept", cwd=repo.b)
    kept = repo.stash_sha()
    assert repo.stash_shas() == [kept, doomed]
    monkeypatch.chdir(repo.a)
    assert mod.main(["recover", doomed, "-m", "duplicate"]) == 0
    assert repo.stash_shas() == [doomed, kept, doomed], "recover prepends a duplicate"
    capsys.readouterr()
    assert mod.main(["prune", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "WARNING" not in out, out
    assert "removed 2" in out, out
    assert repo.stash_shas() == [kept], "both doomed occurrences (and only those) go"


def test_recover_refuses_rather_than_drop_a_diverged_register(repo, monkeypatch, capsys):
    """A ref/reflog divergence must not let `recover` destroy peer entries.

    `git reflog delete refs/stash@{0}` WITHOUT `--updateref` (a bare-git action)
    leaves `refs/stash` pointing at the deleted entry while the OTHER entries
    stay in the reflog. `stash store` no-ops in that state, and dropping the ref
    to force a reflog entry would delete the whole reflog — including the peer
    entry still enumerated there (#8773 review P1). `recover` must refuse, and
    leave both the peer entry and the ref alone.
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\npeer\n")
    repo.git("stash", "push", "-q", "-m", "peer", cwd=repo.b)
    peer = repo.stash_sha()
    repo.dirty(repo.a, "f.txt", "base\nown\n")
    assert repo.lane(repo.a, "push", "-m", "own").returncode == 0
    own = repo.stash_sha()
    assert peer is not None and own is not None and repo.stash_shas() == [own, peer]
    # bare git deletes the TOP reflog entry without moving the ref: refs/stash
    # still resolves to `own`, which is now absent from the register
    repo.git("reflog", "delete", "refs/stash@{0}")
    assert repo.stash_shas() == [peer]
    assert repo.stash_sha() == own
    monkeypatch.chdir(repo.a)
    capsys.readouterr()
    assert mod.main(["recover", own, "-m", "recover own"]) != 0
    assert repo.stash_shas() == [peer], "the peer entry must survive the refusal"
    assert repo.stash_sha() == own, "the diverged ref must be left in place"


def test_recover_releases_a_guard_created_by_another_lane(repo, monkeypatch, capsys):
    """A guard must be released even when another lane does the recovering.

    Lane B's prune retains and guard-pins an orphan; lane A is the one that runs
    `recover`. Unpinning only the current lane's guard left lane B's ref (and the
    object it keeps alive) in place forever, and made B's `list` claim no other
    ref named it (#8773 review P2).
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\norphan\n")
    repo.git("stash", "push", "-q", "-m", "orphan", cwd=repo.b)
    orphan = repo.stash_sha()
    repo.git("reflog", "delete", "--updateref", "refs/stash@{0}")
    assert repo.stash_shas() == []
    assert orphan is not None
    # lane B's prune retains the orphan and guards it
    assert repo.lane(repo.b, "prune", "--apply").returncode == 0
    assert repo.git(
        "for-each-ref", "--format=%(refname)", "refs/pi-lane-prune-guard"
    ).stdout.split(), "lane B must have guard-pinned the orphan"
    # lane A recovers it
    monkeypatch.chdir(repo.a)
    assert mod.main(["recover", orphan, "-m", "rescued"]) == 0
    assert repo.stash_shas() == [orphan]
    assert repo.git(
        "for-each-ref", "--format=%(refname)", "refs/pi-lane-prune-guard"
    ).stdout.strip() == "", "another lane's guard must be released too"


def test_recover_accepts_an_abbreviated_sha(repo, monkeypatch, capsys):
    """`recover` must normalise the sha before the register lookup.

    The register enumerates FULL shas, so an abbreviated sha (the 12-char form the
    tool itself prints) can never match an exact-string membership check. The store
    would succeed yet be read as failure: the entry is left UNPINNED while the user
    is told it is absent, and the next bare `git stash pop` takes it (#8773 review
    P1).
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\nrescued\n")
    repo.git("stash", "push", "-q", "-m", "rescued", cwd=repo.b)
    sha = repo.stash_sha()
    assert sha is not None
    repo.git("reflog", "delete", "--updateref", "refs/stash@{0}")
    assert repo.stash_shas() == []
    monkeypatch.chdir(repo.a)
    capsys.readouterr()
    assert mod.main(["recover", sha[:10], "-m", "rescued"]) == 0
    assert repo.stash_shas() == [sha], "the abbreviated name must resolve to the full entry"
    assert sha in repo.lane_refs().values(), "the entry must be pinned, not left bare"
    assert repo.lane(repo.a, "check").returncode == 0


def test_list_reports_a_retained_guard_as_the_durable_handle(repo):
    """`list` must not call refs/stash a durable alternative for a guard.

    refs/stash is exactly what a later `stash store` displaces, so naming it as the
    other ref that keeps the object alive could steer the user to drop the only
    durable handle (#8773 review P2).
    """
    repo.dirty(repo.b, "f.txt", "base\norphan\n")
    repo.git("stash", "push", "-q", "-m", "orphan", cwd=repo.b)
    repo.git("reflog", "delete", "--updateref", "refs/stash@{0}")
    assert repo.lane(repo.b, "prune", "--apply").returncode == 0
    listed = repo.lane(repo.b, "list")
    assert listed.returncode == 0
    assert "prune guards held" in listed.stdout, listed.stdout
    assert "NOT durable" in listed.stdout, listed.stdout


def test_recover_does_not_displace_an_unrelated_orphan(repo, monkeypatch, capsys):
    """`recover` must not move an unpinned orphan's only handle.

    When the register is empty and `refs/stash` points at a DIFFERENT entry (an
    unpinned orphan — the state `prune` retains and guard-pins), `git stash store`
    moves the ref off that orphan. `prune` protects it; `recover` displaced it
    silently, leaving the orphan gc-eligible (#8773 review P1).
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\ny\n")
    repo.git("stash", "push", "-q", "-m", "y", cwd=repo.b)
    y = repo.stash_sha()
    repo.git("stash", "drop", "-q", cwd=repo.b)
    repo.dirty(repo.b, "f.txt", "base\nx\n")
    repo.git("stash", "push", "-q", "-m", "x", cwd=repo.b)
    x = repo.stash_sha()
    repo.git("reflog", "expire", "--expire=now", "--all")
    assert repo.stash_shas() == []
    assert repo.stash_sha() == x
    assert y is not None and x is not None and y != x
    monkeypatch.chdir(repo.a)
    capsys.readouterr()
    assert mod.main(["recover", y, "-m", "y rescue"]) == 0
    assert repo.stash_shas() == [y]
    named = repo.git("for-each-ref", "--format=%(objectname)").stdout.split()
    assert x in named, "the unrelated orphan must still be named by a ref"


def test_recover_restores_the_ref_when_the_second_store_fails(repo, monkeypatch, capsys):
    """A failed re-store must leave `refs/stash` pointing at the entry again.

    The plan claims this; without a test, the branch that puts the handle back
    after a drop+failed-store can regress unnoticed (#8773 review P2).
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\norphan\n")
    repo.git("stash", "push", "-q", "-m", "orphan", cwd=repo.b)
    orphan = repo.stash_sha()
    repo.git("reflog", "delete", "--updateref", "refs/stash@{0}")
    assert repo.stash_shas() == []
    assert orphan is not None
    monkeypatch.chdir(repo.a)
    real_git = mod._git
    calls = {"n": 0}

    def wrapping_git(*args, check=True, stdin=None):
        if args[:2] == ("stash", "store"):
            calls["n"] += 1
            if calls["n"] == 2:
                class _Failed:
                    returncode = 1
                    stdout = ""
                    stderr = "injected store failure"
                return _Failed()
        return real_git(*args, check=check, stdin=stdin)

    monkeypatch.setattr(mod, "_git", wrapping_git)
    capsys.readouterr()
    assert mod.main(["recover", orphan, "-m", "rescue"]) != 0
    assert calls["n"] == 2
    assert repo.stash_sha() == orphan, "the ref must be put back after a failed re-store"


def test_recover_protects_a_diverged_registers_head(repo, monkeypatch, capsys):
    """A non-empty register must not exempt its un-enumerated ref head.

    `git reflog delete refs/stash@{0}` WITHOUT `--updateref` over a register that
    still holds other entries leaves `refs/stash` on an unpinned, un-enumerated
    head. Storing a rescued entry moves that ref off it, so the head must be
    guard-pinned even though the register is NOT empty (#8773 review P1).
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\nz\n")
    repo.git("stash", "push", "-q", "-m", "z", cwd=repo.b)
    z = repo.stash_sha()
    repo.git("stash", "drop", "-q", cwd=repo.b)
    repo.dirty(repo.b, "f.txt", "base\nx\n")
    repo.git("stash", "push", "-q", "-m", "x", cwd=repo.b)
    x = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\ny\n")
    repo.git("stash", "push", "-q", "-m", "y", cwd=repo.b)
    y = repo.stash_sha()
    # bare git drops the TOP reflog entry without moving the ref: refs/stash
    # stays on unpinned `y` while the register still enumerates `x`
    repo.git("reflog", "delete", "refs/stash@{0}")
    assert repo.stash_shas() == [x]
    assert repo.stash_sha() == y
    monkeypatch.chdir(repo.a)
    capsys.readouterr()
    assert mod.main(["recover", z, "-m", "z rescue"]) == 0
    assert repo.stash_shas() == [z, x]
    named = repo.git("for-each-ref", "--format=%(objectname)").stdout.split()
    assert y in named, "the diverged head must still be named by a ref"


def test_recover_does_not_guard_an_already_pinned_head(repo, monkeypatch, capsys):
    """A head another ref already names needs no guard (which would leak).

    `_guard_release_all` only ever frees guards for the recovered sha, so a guard
    created for a head that was already pinned under `refs/lane` would live
    forever, contradicting the plan's one-lifetime rule (#8773 review P2).
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\nz\n")
    repo.git("stash", "push", "-q", "-m", "z", cwd=repo.b)
    z = repo.stash_sha()
    repo.git("stash", "drop", "-q", cwd=repo.b)
    repo.dirty(repo.a, "f.txt", "base\ny\n")
    assert repo.lane(repo.a, "push", "-m", "y").returncode == 0
    y = repo.stash_sha()
    repo.git("reflog", "expire", "--expire=now", "--all")
    assert repo.stash_shas() == []
    assert repo.stash_sha() == y
    monkeypatch.chdir(repo.a)
    capsys.readouterr()
    assert mod.main(["recover", z, "-m", "z rescue"]) == 0
    guards = repo.git(
        "for-each-ref", "--format=%(refname) %(objectname)", "refs/pi-lane-prune-guard"
    ).stdout.split()
    assert y not in guards, "an already-pinned head must not get a guard ref"


def test_recover_refuses_when_the_orphan_cannot_be_pinned(repo, monkeypatch, capsys):
    """An unpinnable orphan must refuse the store (fail-closed), not displace it.

    The docstring claims this; without a test a fail-open regression passes the
    suite (#8773 review P2).
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\nz\n")
    repo.git("stash", "push", "-q", "-m", "z", cwd=repo.b)
    z = repo.stash_sha()
    repo.git("stash", "drop", "-q", cwd=repo.b)
    repo.dirty(repo.b, "f.txt", "base\norphan\n")
    repo.git("stash", "push", "-q", "-m", "orphan", cwd=repo.b)
    orphan = repo.stash_sha()
    repo.git("reflog", "delete", "--updateref", "refs/stash@{0}")
    assert repo.stash_shas() == []
    monkeypatch.chdir(repo.a)
    monkeypatch.setattr(mod, "_guard_pin", lambda *a, **k: None)
    capsys.readouterr()
    assert mod.main(["recover", z, "-m", "z rescue"]) != 0
    assert repo.stash_shas() == [], "the register must be left untouched"
    assert repo.stash_sha() == orphan, "the orphan must keep its handle"


def test_prune_refuses_when_a_kept_entry_cannot_be_guarded(repo, monkeypatch, capsys):
    """A failed guard must abort the prune before any delete (fail-closed).

    The code refuses to remove anything while a kept entry cannot be protected;
    without a test a fail-open regression passes the suite (#8773 review P2).
    """
    mod = _load_tool()
    repo.dirty(repo.a, "f.txt", "base\ndoomed\n")
    assert repo.lane(repo.a, "push", "-m", "doomed").returncode == 0
    doomed = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\nkept\n")
    repo.git("stash", "push", "-q", "-m", "kept", cwd=repo.b)
    kept = repo.stash_sha()
    assert repo.stash_shas() == [kept, doomed]
    monkeypatch.chdir(repo.a)
    monkeypatch.setattr(mod, "_guard_pin", lambda *a, **k: None)
    capsys.readouterr()
    assert mod.main(["prune", "--apply"]) != 0
    assert repo.stash_shas() == [kept, doomed], "nothing may be deleted without a guard"
    assert repo.git(
        "for-each-ref", "--format=%(refname)", "refs/pi-lane-prune-guard"
    ).stdout.strip() == ""


def test_prune_refuses_a_diverged_registers_head(repo, monkeypatch, capsys):
    """`prune` must refuse a register diverged from its ref, not drop its head.

    A bare `git reflog delete` without `--updateref` leaves `refs/stash` on a sha
    the register no longer enumerates. That head is named by no other ref, and
    the first `reflog delete --updateref` would MOVE the ref to the reflog's new
    top — a single-threaded, silent loss of a peer's entry. It cannot be
    re-listed either (`stash store` no-ops while the ref already equals the sha),
    so the prune refuses and leaves everything alone (#8773 reviews P1, P2).
    """
    mod = _load_tool()
    repo.dirty(repo.a, "f.txt", "base\ndoomed\n")
    assert repo.lane(repo.a, "push", "-m", "doomed").returncode == 0
    doomed = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\nkept\n")
    repo.git("stash", "push", "-q", "-m", "kept", cwd=repo.b)
    kept = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\norphan\n")
    repo.git("stash", "push", "-q", "-m", "orphan", cwd=repo.b)
    orphan = repo.stash_sha()
    assert repo.stash_shas() == [orphan, kept, doomed]
    # bare git drops the TOP reflog entry without moving the ref: refs/stash
    # stays on `orphan`, which is no longer enumerated
    repo.git("reflog", "delete", "refs/stash@{0}")
    assert repo.stash_shas() == [kept, doomed]
    assert repo.stash_sha() == orphan
    monkeypatch.chdir(repo.a)
    capsys.readouterr()
    assert mod.main(["prune", "--apply"]) != 0
    assert repo.stash_shas() == [kept, doomed], "nothing may be deleted from a diverged register"
    assert repo.stash_sha() == orphan, "the diverged head keeps its ref"
    named = repo.git("for-each-ref", "--format=%(objectname)").stdout.split()
    assert named.count(orphan) == 1, "and the head still exists"
    assert repo.git(
        "for-each-ref", "--format=%(refname)", "refs/pi-lane-prune-guard"
    ).stdout.strip() == "", "a refused prune must not leave a guard behind"


def test_prune_refuses_a_diverged_register_with_nothing_to_reclaim(repo, monkeypatch, capsys):
    """The refusal must not be skipped by the `not doomed` early return.

    A diverged register whose entries are all unpinned has nothing to reclaim, so
    the `if not doomed` return would exit 0 while the summary line says the prune
    REFUSES — breaking both the promise and its exit code (#8773 review P2).
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\ny\n")
    repo.git("stash", "push", "-q", "-m", "y", cwd=repo.b)
    y = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\nx\n")
    repo.git("stash", "push", "-q", "-m", "x", cwd=repo.b)
    x = repo.stash_sha()
    repo.git("reflog", "delete", "refs/stash@{0}")
    assert repo.stash_shas() == [y]
    assert repo.stash_sha() == x
    monkeypatch.chdir(repo.a)
    capsys.readouterr()
    assert mod.main(["prune", "--apply"]) != 0
    assert repo.stash_shas() == [y], "a diverged register is left alone"
    assert repo.stash_sha() == x


def test_show_reports_the_published_exit_codes(repo):
    """`show` must honour the documented exit-code set, and have coverage.

    It used to return git's raw status (128) and skipped the dangling-pin guard
    the other retrieval commands have, so a wrapper switching on 0/1/2/3
    mis-classified it (#8773 review P2).
    """
    repo.dirty(repo.a, "f.txt", "base\nshow\n")
    assert repo.lane(repo.a, "push", "-m", "show").returncode == 0
    shown = repo.lane(repo.a, "show")
    assert shown.returncode == 0, _fail(shown)
    # a missing entry is a named state error (2), as for apply/pop
    assert repo.lane(repo.a, "show", "99").returncode == 2
    # a ref pointing at a commit that is NOT a stash is a git failure (3), not 128
    ref = next(r for r in repo.lane_refs() if r.endswith("/stash/1"))
    prefix = ref.rsplit("/", 1)[0]
    plain = repo.git("rev-parse", "HEAD", cwd=repo.a).stdout.strip()
    repo.git("update-ref", f"{prefix}/stash/9", plain)
    assert repo.lane(repo.a, "show", "9").returncode == 3


def test_list_attributes_by_lane_key_not_branch(repo):
    """`list` must agree with `check`: ownership is the lane key, not the branch.

    A branch name identifies a branch, never a lane, so a branch-only mark called
    this lane's own pinned entry foreign after a checkout, and a same-branch
    marker-less peer entry safe (#8773 review P2).
    """
    repo.dirty(repo.a, "f.txt", "base\nown\n")
    assert repo.lane(repo.a, "push", "-m", "own").returncode == 0
    repo.git("checkout", "-q", "-b", "baseline-check", cwd=repo.a)
    listed = repo.lane(repo.a, "list")
    assert listed.returncode == 0
    assert "not provably this lane's" not in listed.stdout, listed.stdout

    # a marker-less bare entry on the SAME branch must not read as safe
    repo.dirty(repo.a, "f.txt", "base\nbare\n")
    repo.git("stash", "push", "-q", "-m", "Bare local note", cwd=repo.a)
    listed = repo.lane(repo.a, "list")
    assert "FOREIGN?" in listed.stdout, listed.stdout
    assert "not provably this lane's" in listed.stdout, listed.stdout
    assert repo.lane(repo.a, "check").returncode == 1


def test_prune_dry_run_refuses_a_diverged_register(repo, monkeypatch, capsys):
    """The diverged-register refusal must not be gated on --apply.

    The refusal is unconditional; a mutation re-restricting it to --apply leaves
    every other test green (#8773 review P3).
    """
    mod = _load_tool()
    repo.dirty(repo.b, "f.txt", "base\ny\n")
    repo.git("stash", "push", "-q", "-m", "y", cwd=repo.b)
    y = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\nx\n")
    repo.git("stash", "push", "-q", "-m", "x", cwd=repo.b)
    x = repo.stash_sha()
    repo.git("reflog", "delete", "refs/stash@{0}")
    assert repo.stash_shas() == [y]
    assert repo.stash_sha() == x
    monkeypatch.chdir(repo.a)
    capsys.readouterr()
    assert mod.main(["prune"]) != 0, "a dry run must refuse a diverged register too"
    assert repo.stash_shas() == [y]


# ── the repair door must not re-open the #8051 hole (#8773 review P1) ───────

def test_recover_refuses_a_peer_lanes_pinned_entry(repo):
    """`recover` must not adopt a peer lane's entry through the repair door.

    `recover` pins its argument under THIS lane's key. Without an ownership check
    a lane could import a peer's entry, be told by `check` that the entry is its
    own (the pin it just made), and take the peer's work with a bare
    `git stash pop` — the incident this tool exists to stop, re-entered through
    the command whose whole purpose is repair (#8773 review P1).
    """
    repo.dirty(repo.a, "f.txt", "base\na-A\n")
    assert repo.lane(repo.a, "push", "-m", "A work").returncode == 0
    sha_a = repo.stash_sha()
    repo.dirty(repo.b, "f.txt", "base\nb-B\n")
    (repo.b / "uB.txt").write_text("B untracked\n")
    assert repo.lane(repo.b, "push", "-u", "-m", "B work").returncode == 0
    sha_b = repo.stash_sha()
    assert sha_a and sha_b and sha_a != sha_b
    before = repo.lane_refs()

    cp = repo.lane(repo.a, "recover", sha_b, "-m", "stolen")
    assert cp.returncode == 2, _fail(cp)
    assert "refusing to recover" in cp.stderr, cp.stderr
    assert repo.lane_refs() == before, "the refusal must not pin a peer's entry"
    assert repo.stash_shas() == [sha_b, sha_a], "the refusal must not move the register"

    # the entry stays a HAZARD for lane A — the refusal did not launder it
    check = repo.lane(repo.a, "check")
    assert check.returncode == 1, _fail(check)
    assert "SAFE" not in check.stdout, check.stdout

    # and the peer can still restore its own work (the entry was untouched)
    assert repo.lane(repo.b, "pop").returncode == 0
    assert (repo.b / "uB.txt").exists()


def test_recover_refuses_a_peer_marker_after_its_pin_is_released(repo):
    """The refusal must also work when only the peer's MESSAGE MARKER remains.

    A peer can release its lane ref without applying the entry (`drop --yes`), so
    the foreign-pin check alone is not enough: the object's own subject still
    names the peer lane and must refuse an adoption (#8773 review P1).
    """
    repo.dirty(repo.b, "f.txt", "base\nb-B\n")
    assert repo.lane(repo.b, "push", "-m", "B work").returncode == 0
    sha_b = repo.stash_sha()
    assert sha_b
    assert repo.lane(repo.b, "drop", "1", "--yes").returncode == 0
    assert repo.lane_refs() == {}, "precondition: the peer's pin is gone"

    cp = repo.lane(repo.a, "recover", sha_b, "-m", "stolen")
    assert cp.returncode == 2, _fail(cp)
    assert "refusing to recover" in cp.stderr, cp.stderr
    assert repo.lane_refs() == {}, "the refusal must not pin a peer-marked entry"
    assert repo.stash_shas() == [sha_b], "the register must be untouched"


def test_check_fails_closed_when_a_local_pin_claims_a_peer_marked_entry(repo):
    """A LOCAL pin must not launder a peer's marker — the double-pin state.

    `check` used to test "is this sha one of my pins" BEFORE it read the marker,
    so the state a cross-lane `recover` used to create (one sha pinned by both
    lanes) was reported SAFE while a bare `git stash pop` took the peer's work
    (#8773 review P1). A foreign lane ref is decisive for the same reason.
    """
    repo.dirty(repo.a, "f.txt", "base\na-A\n")
    assert repo.lane(repo.a, "push", "-m", "A work").returncode == 0
    a_key = next(iter(repo.stash_pins())).split("/")[2]
    repo.dirty(repo.b, "f.txt", "base\nb-B\n")
    assert repo.lane(repo.b, "push", "-m", "B work").returncode == 0
    sha_b = repo.stash_sha()
    assert sha_b
    # the exact state the old `recover` left behind: B's entry ALSO pinned by A
    repo.git("update-ref", f"refs/lane/{a_key}/stash/99", sha_b)
    assert sha_b in repo.stash_pins().values()

    check = repo.lane(repo.a, "check")
    assert check.returncode == 1, _fail(check)
    assert "SAFE" not in check.stdout, check.stdout

    # the hazard the detector refuses to clear is real, not a conservative guess
    bare = repo.git("stash", "pop", cwd=repo.a, check=False)
    assert bare.returncode == 0, _fail(bare)
    assert "b-B" in (repo.a / "f.txt").read_text()


def test_list_marks_a_peers_pinned_entry_foreign(repo):
    """`list` must not label a peer's pinned entry exactly like this lane's own.

    `_pinned_shas()` is repo-global, so the old `pinned` mark came FIRST and the
    lane-key classifier was never reached: this lane's own pinned row and a
    peer's came out byte-identical, contradicting the commit's own claim that
    `list` attributes by lane key (#8773 review P2).
    """
    repo.dirty(repo.a, "f.txt", "base\na-A\n")
    assert repo.lane(repo.a, "push", "-m", "A work").returncode == 0
    repo.dirty(repo.b, "f.txt", "base\nb-B\n")
    assert repo.lane(repo.b, "push", "-m", "B work").returncode == 0

    listed = repo.lane(repo.a, "list")
    assert listed.returncode == 0, _fail(listed)
    assert "OWN" in listed.stdout, listed.stdout
    assert "FOREIGN" in listed.stdout, listed.stdout
    # the old mark labelled BOTH rows `pinned  `; no row may carry it now
    assert "pinned  " not in listed.stdout, listed.stdout
    # the peer's pinned row is the FOREIGN one
    foreign_rows = [ln for ln in listed.stdout.splitlines() if "FOREIGN" in ln]
    assert foreign_rows, listed.stdout
    assert all("(pinned)" in ln for ln in foreign_rows), foreign_rows


def test_check_is_safe_for_this_lanes_applied_markerless_entry(repo):
    """`pop` moves the pin to the applied ref — `check` must still call it ours.

    `pop` releases the lane PIN and records the apply at
    `refs/lane/<key>/applied/<sha>`; for a marker-less entry this lane recovered
    (bare-git debris), that applied record is the ONLY ownership evidence left.
    Reading only `stash` pins made `check` report HAZARD — and `list` FOREIGN? —
    on the lane's own already-restored work, with a reason that claimed no ref
    named it while the applied ref did (#8773 review P2).
    """
    repo.dirty(repo.a, "f.txt", "base\nrescued\n")
    repo.git("stash", "push", "-q", "-m", "On feat/a: note", cwd=repo.a)
    dropped = repo.stash_sha()
    repo.git("stash", "drop", cwd=repo.a)
    # recover marker-less debris, then apply it: the pin becomes an applied record
    assert repo.lane(repo.a, "recover", dropped, "-m", "rescued note").returncode == 0
    assert repo.lane(repo.a, "pop").returncode == 0
    assert repo.applied_refs(), "precondition: the apply left a durable record"
    assert repo.stash_pins() == {}, "precondition: the pin was released"

    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "SAFE" in check.stdout, check.stdout

    listed = repo.lane(repo.a, "list")
    assert listed.returncode == 0, _fail(listed)
    assert "OWN" in listed.stdout, listed.stdout
    assert "FOREIGN" not in listed.stdout, listed.stdout
    assert "not provably this lane's" not in listed.stdout, listed.stdout


# ── advertised subcommands/flags with no coverage (#8773 review P2) ─────────

def test_apply_keeps_the_pin_and_writes_no_applied_record(repo):
    """`apply` must KEEP the lane pin and record nothing — only `pop` releases it.

    `apply` is an advertised subcommand ("apply ... keeping the pin") and is the
    `pop=False` branch of `cmd_apply`, but it had no test at all: a regression that
    released the pin or wrote the durable APPLIED record on `apply` would leave the
    whole suite green (#8773 review P2).
    """
    repo.dirty(repo.a, "f.txt", "base\napplied\n")
    assert repo.lane(repo.a, "push", "-m", "apply me").returncode == 0
    sha = repo.stash_sha()
    assert sha
    assert repo.lane(repo.a, "apply").returncode == 0
    assert "applied" in (repo.a / "f.txt").read_text()
    assert sha in repo.stash_pins().values(), "apply must keep the lane pin"
    assert repo.applied_refs() == {}, "apply must not record an APPLIED entry"
    assert repo.stash_shas() == [sha], "apply must not touch the shared register"

    # the pin is still the handle: `pop` releases it and records the apply
    repo.git("checkout", "--", "f.txt", cwd=repo.a)
    assert repo.lane(repo.a, "pop").returncode == 0
    assert repo.stash_pins() == {}
    assert repo.applied_refs(), "pop must record the apply"


def test_pop_index_restores_the_staged_state(repo):
    """`--index` must restore the INDEX, not just the worktree (untested flag)."""
    repo.dirty(repo.a, "f.txt", "base\nstaged\n")  # `dirty` stages the change
    assert repo.lane(repo.a, "push", "-m", "staged work").returncode == 0
    assert repo.status(repo.a) == ""
    assert repo.lane(repo.a, "pop", "--index").returncode == 0
    assert "staged" in (repo.a / "f.txt").read_text()
    assert "M  f.txt" in repo.status(repo.a), repo.status(repo.a)


def test_show_patch_prints_the_lane_entry_diff(repo):
    """`show -p` must print the patch for a lane entry (untested flag)."""
    repo.dirty(repo.a, "f.txt", "base\nshown\n")
    assert repo.lane(repo.a, "push", "-m", "show me").returncode == 0
    shown = repo.lane(repo.a, "show", "-p")
    assert shown.returncode == 0, _fail(shown)
    assert "shown" in shown.stdout, shown.stdout


def test_push_with_a_pathspec_pins_only_the_named_path(repo):
    """`push -- <pathspec>` must stash only that path (untested advertised form)."""
    repo.dirty(repo.a, "f.txt", "base\npath-one\n")
    (repo.a / "other.txt").write_text("other\n")
    repo.git("add", "other.txt", cwd=repo.a)
    assert repo.lane(repo.a, "push", "-m", "one path", "--", "f.txt").returncode == 0
    assert "path-one" not in (repo.a / "f.txt").read_text()
    assert (repo.a / "other.txt").exists(), "the unnamed path must stay in the worktree"
    assert "A  other.txt" in repo.status(repo.a), repo.status(repo.a)
    assert repo.lane(repo.a, "pop").returncode == 0
    assert "path-one" in (repo.a / "f.txt").read_text()


def test_push_all_includes_ignored_files(repo):
    """`push -a` must capture ignored files too (untested advertised flag)."""
    (repo.a / ".gitignore").write_text("ignored.txt\n")
    repo.git("add", ".gitignore", cwd=repo.a)
    repo.git("commit", "-qm", "ignore", cwd=repo.a)
    (repo.a / "ignored.txt").write_text("ignored work\n")
    assert repo.lane(repo.a, "push", "-a", "-m", "with ignored").returncode == 0
    assert not (repo.a / "ignored.txt").exists()
    assert repo.lane(repo.a, "pop").returncode == 0
    assert (repo.a / "ignored.txt").exists()


def test_prune_dry_run_does_not_create_a_guard_ref(repo):
    """A dry-run `prune` must not mutate the ref store.

    In the emptied-register unpinned-orphan state the dry run guard-pinned the
    object BEFORE the `--apply` gate, so an inspection command silently wrote a ref
    that only `recover` or a manual `update-ref -d` can clear (#8773 review P2).
    """
    repo.dirty(repo.b, "f.txt", "base\norphan\n")
    repo.git("stash", "push", "-q", "-m", "orphan", cwd=repo.b)
    sha = repo.stash_sha()
    repo.git("reflog", "expire", "--expire=now", "--all")
    assert repo.stash_shas() == [] and repo.stash_sha() == sha

    dry = repo.lane(repo.a, "prune")
    assert dry.returncode == 0, _fail(dry)
    assert "recover" in dry.stdout, dry.stdout
    assert repo.git(
        "for-each-ref", "--format=%(refname)", "refs/pi-lane-prune-guard"
    ).stdout.strip() == "", "a dry run must not write a guard ref"
    assert repo.stash_sha() == sha, "a dry run must not change refs/stash"


# ── round-4 review P2s ─────────────────────────────────────────���───────────

def test_interpreter_guard_exits_2_not_the_hazard_code(monkeypatch):
    """The version guard must exit 2 — 1 is `check`'s HAZARD verdict.

    `raise SystemExit(<str>)` always exits 1, so an old interpreter was
    indistinguishable, by exit code, from a foreign stash entry. The generic
    entry-point guard test accepts either shape and never asserts the integer, so
    without this case the collision can silently return (#8773 review P2).
    """
    import importlib.util

    monkeypatch.setattr(sys, "version_info", (3, 9, 6))
    spec = importlib.util.spec_from_file_location("lane_stash_old_python", TOOL)
    mod = importlib.util.module_from_spec(spec)
    with pytest.raises(SystemExit) as refusal:
        spec.loader.exec_module(mod)
    assert refusal.value.code == 2, (
        f"the guard must exit the documented environment code (2), not "
        f"{refusal.value.code!r} — 1 is `check`'s HAZARD verdict"
    )


def test_list_reports_a_guard_left_by_another_lane(repo):
    """A guard must be visible from every lane, including one whose worktree is gone.

    `prune --apply` creates a guard automatically for a retained orphan, and the
    guard outlives `git worktree remove`. Enumerating only THIS lane's guards left
    it invisible — a permanent gc-blocking ref in the shared git dir (#8773 P2).
    """
    repo.dirty(repo.b, "f.txt", "base\norphan\n")
    repo.git("stash", "push", "-q", "-m", "orphan", cwd=repo.b)
    # routine maintenance empties the register while refs/stash keeps resolving
    repo.git("reflog", "expire", "--expire=now", "--all")
    assert repo.lane(repo.b, "prune", "--apply").returncode == 0
    guards = repo.git(
        "for-each-ref", "--format=%(refname)", "refs/pi-lane-prune-guard"
    ).stdout.split()
    assert guards, "precondition: lane B's prune retained and guarded the orphan"
    b_key = guards[0].split("/")[2]

    listed = repo.lane(repo.a, "list")
    assert listed.returncode == 0, _fail(listed)
    assert guards[0] in listed.stdout, listed.stdout
    assert f"[lane {b_key} — not this lane]" in listed.stdout, listed.stdout


def test_prune_releases_a_guard_left_under_another_lane(repo, monkeypatch, capsys):
    """A guard is redundant once its sha is back in the register — under ANY key.

    Another lane (or a since-removed worktree) can leave a guard for an entry this
    prune is about to keep. Releasing only this prune's own guard left that ref
    blocking gc forever (#8773 review P2).
    """
    mod = _load_tool()
    repo.dirty(repo.a, "f.txt", "base\ndoomed\n")
    assert repo.lane(repo.a, "push", "-m", "doomed").returncode == 0
    repo.dirty(repo.b, "f.txt", "base\nkept\n")
    repo.git("stash", "push", "-q", "-m", "kept", cwd=repo.b)
    kept = repo.stash_sha()
    assert kept
    ghost = f"refs/pi-lane-prune-guard/ghost-00000000/{kept}"
    repo.git("update-ref", ghost, kept)

    monkeypatch.chdir(repo.a)
    capsys.readouterr()
    assert mod.main(["prune", "--apply"]) == 0
    remaining = repo.git(
        "for-each-ref", "--format=%(refname)", "refs/pi-lane-prune-guard"
    ).stdout.split()
    assert ghost not in remaining, f"a foreign guard for a live entry leaked: {remaining}"


def test_an_unreadable_ref_list_is_a_state_error_not_safe(repo, monkeypatch):
    """A failed `for-each-ref --points-at` must fail closed, never report SAFE.

    That read decides WHO OWNS an entry; collapsing its failure into "no peer ref
    names it" is the same fail-open as an unreadable shared register (#8773 P2).
    """
    mod = _load_tool()
    repo.dirty(repo.a, "f.txt", "base\na-A\n")
    assert repo.lane(repo.a, "push", "-m", "A work").returncode == 0
    monkeypatch.chdir(repo.a)
    real_git = mod._git

    def fake_git(*args, check=True, stdin=None):
        if args[:1] == ("for-each-ref",) and any(
                a.startswith("--points-at=") for a in args):
            return subprocess.CompletedProcess(
                ["git", *args], 1, "", "fatal: cannot read refs"
            )
        return real_git(*args, check=check, stdin=stdin)

    monkeypatch.setattr(mod, "_git", fake_git)
    rc = mod.main(["check", "--json"])
    assert rc == 2, f"an unreadable ref list is a state error (exit 2), got {rc}"


def test_push_locates_our_entry_across_a_concurrent_peer_push(repo, monkeypatch, capsys):
    """A peer push between OUR `git stash push` and the locate must not mis-identify it.

    The nonce locate is the load-bearing race claim of the whole creation path and
    had no test (#8773 review P2).
    """
    mod = _load_tool()
    repo.dirty(repo.a, "f.txt", "base\na-A\n")
    real_git = mod._git
    fired = {"done": False}

    def wrapping_git(*args, check=True, stdin=None):
        if args[:2] == ("log", "--walk-reflogs") and not fired["done"]:
            fired["done"] = True
            # a peer push lands AFTER our `git stash push`, BEFORE the locate
            repo.dirty(repo.b, "f.txt", "base\nb-B\n")
            repo.git("stash", "push", "-q", "-m", "peer", cwd=repo.b)
        return real_git(*args, check=check, stdin=stdin)

    monkeypatch.chdir(repo.a)
    monkeypatch.setattr(mod, "_git", wrapping_git)
    capsys.readouterr()
    assert mod.main(["push", "-m", "A work"]) == 0
    sha_b = repo.stash_sha()
    pins = repo.stash_pins()
    assert len(pins) == 1, pins
    pinned = next(iter(pins.values()))
    assert pinned != sha_b, "push pinned the peer's entry that landed during its locate"
    assert repo.lane(repo.a, "pop").returncode == 0
    assert "a-A" in (repo.a / "f.txt").read_text()


def test_a_pin_that_is_not_a_readable_commit_is_refused_and_marked(repo):
    """A pin whose object is unreadable must refuse, never crash (post-gc case).

    `apply`/`pop`/`show` guard the object and `list` marks MISSING OBJECT. The
    path had no test, though the plan's checklist claimed it did (#8773 review P2).
    """
    repo.dirty(repo.a, "f.txt", "base\nx\n")
    assert repo.lane(repo.a, "push", "-m", "x").returncode == 0
    a_key = next(iter(repo.stash_pins())).split("/")[2]
    # a real object that is NOT a commit: the same `_commit_alive` false branch
    blob = repo.git("hash-object", "-w", "f.txt", cwd=repo.a).stdout.strip()
    assert blob
    repo.git("update-ref", f"refs/lane/{a_key}/stash/9", blob)

    listed = repo.lane(repo.a, "list")
    assert listed.returncode == 0, _fail(listed)
    assert "MISSING OBJECT" in listed.stdout, listed.stdout
    assert repo.lane(repo.a, "pop", "9").returncode == 2
    assert repo.lane(repo.a, "show", "9").returncode == 2


def test_detached_head_push_check_and_pop(repo):
    """A detached HEAD must not break push/locate/check/restore."""
    repo.git("checkout", "--detach", cwd=repo.a)
    repo.dirty(repo.a, "f.txt", "base\ndetached\n")
    assert repo.lane(repo.a, "push", "-m", "detached work").returncode == 0
    check = repo.lane(repo.a, "check")
    assert check.returncode == 0, _fail(check)
    assert "SAFE" in check.stdout, check.stdout
    assert repo.lane(repo.a, "pop").returncode == 0
    assert "detached" in (repo.a / "f.txt").read_text()


# ── round-5 review P2s ──────────────────────────────────────────────────────

def test_prune_retains_each_guard_by_its_own_ref_when_the_register_is_unreadable(
    repo, monkeypatch, capsys,
):
    """A retained guard's message must name ITS ref and must not invent a loss.

    The `finally` printed an unrelated loop variable for `ref`, so with two guarded
    entries it named the last one's ref for both — and asserted the entry was not
    in a register that had merely failed to read (#8773 review P2).
    """
    mod = _load_tool()
    _four_entry_register(repo)
    monkeypatch.chdir(repo.a)
    real_git = mod._git
    real_register = mod._shared_register
    state = {"fail": False}

    def wrapping_git(*args, check=True, stdin=None):
        cp = real_git(*args, check=check, stdin=stdin)
        if args[:2] == ("reflog", "delete") and cp.returncode == 0:
            state["fail"] = True
        return cp

    def failing_register():
        if state["fail"]:
            raise mod.LaneStashError("injected register read failure")
        return real_register()

    monkeypatch.setattr(mod, "_git", wrapping_git)
    monkeypatch.setattr(mod, "_shared_register", failing_register)
    capsys.readouterr()
    rc = mod.main(["prune", "--apply"])
    assert rc == 2, rc
    out = capsys.readouterr().out
    guard_refs = repo.git(
        "for-each-ref", "--format=%(refname)", "refs/pi-lane-prune-guard"
    ).stdout.split()
    assert len(guard_refs) == 2, guard_refs
    for ref in guard_refs:
        assert ref in out, f"the retention message did not name its own guard: {out}"
    assert "it is not back in the shared register" not in out, (
        "the register could not be READ; the message must not claim the entry is missing"
    )


def test_an_unreadable_pin_list_is_a_state_error_not_debris(repo, monkeypatch):
    """A failed pin enumeration must raise, not report "pinned by no lane ref".

    `_pinned_shas`/`_applied_shas` collapsed a non-zero `for-each-ref` into an
    empty set, so `check` told the operator a pinned entry was bare-git debris
    (#8773 review P2).
    """
    mod = _load_tool()
    repo.dirty(repo.a, "f.txt", "base\na-A\n")
    assert repo.lane(repo.a, "push", "-m", "A work").returncode == 0
    monkeypatch.chdir(repo.a)
    real_git = mod._git

    def fake_git(*args, check=True, stdin=None):
        if args[:1] == ("for-each-ref",) and any(a.endswith("/stash/*") for a in args):
            return subprocess.CompletedProcess(
                ["git", *args], 128, "", "fatal: cannot read refs"
            )
        return real_git(*args, check=check, stdin=stdin)

    monkeypatch.setattr(mod, "_git", fake_git)
    rc = mod.main(["check", "--json"])
    assert rc == 2, f"an unreadable pin list is a state error (exit 2), got {rc}"


def test_prune_releases_an_applied_record_whose_entry_is_already_gone(repo):
    """An applied record must not outlive its register entry by another route.

    `pop` writes one applied record per cycle; if the register entry then leaves
    via a peer's `stash drop`/`clear` or a reflog expiry, the record survived
    forever as a permanent, gc-blocking ref in the SHARED git dir that `list`
    never reported (#8773 review P2).
    """
    repo.dirty(repo.a, "f.txt", "base\nrestored\n")
    assert repo.lane(repo.a, "push", "-m", "restored").returncode == 0
    assert repo.lane(repo.a, "pop").returncode == 0
    records = repo.applied_refs()
    assert len(records) == 1, records

    # `list` must show the record before anything reaps it
    listed = repo.lane(repo.a, "list")
    assert "applied records held" in listed.stdout, listed.stdout

    # the register entry leaves by a route that is NOT this prune
    repo.git("stash", "clear", cwd=repo.a)
    assert repo.stash_shas() == [] and repo.stash_sha() is None

    assert repo.lane(repo.a, "prune", "--apply").returncode == 0
    assert repo.applied_refs() == {}, "the applied record outlived its register entry"


# ── round-6 review P1s ──────────────────────────────────────────────────────

def test_recover_refuses_an_unpinned_bare_entry_already_in_the_register(repo):
    """`recover` must not mint ownership of a marker-less bare entry in the register.

    A bare-git entry (marker-less, unpinned) is ALREADY enumerated, so nothing was
    dropped — but `recover` pinned it to this lane and `check` flipped HAZARD ->
    SAFE, re-opening the #2654 incident for exactly the class this tool exists to
    protect (#8773 review P1).
    """
    repo.dirty(repo.b, "f.txt", "base\nb-bare\n")
    repo.git("stash", "push", "-q", "-m", "B bare work", cwd=repo.b)
    sha_b = repo.stash_sha()
    assert sha_b and repo.stash_shas() == [sha_b]
    assert repo.lane(repo.a, "check").returncode == 1

    cp = repo.lane(repo.a, "recover", sha_b)
    assert cp.returncode == 2, _fail(cp)
    assert "ALREADY in the shared register" in cp.stderr, cp.stderr
    assert repo.stash_pins() == {}, "the refusal must not pin a peer's entry"
    assert repo.lane(repo.a, "check").returncode == 1, "the refusal must not launder HAZARD"
    assert repo.stash_shas() == [sha_b], "the refusal must not move the register"

    # the explicit assertion escape still works — and is the operator's choice
    forced = repo.lane(repo.a, "recover", sha_b, "-m", "deliberate rescue")
    assert forced.returncode == 0, _fail(forced)
    assert repo.stash_pins(), "an explicit -m recovery must still pin"


def test_list_and_check_tolerate_a_non_utf8_stash_message(repo):
    """A non-UTF-8 stash message must not crash the tool (git accepts them).

    `_git` decoded strictly, so ONE such subject in the repo-global register
    raised `UnicodeDecodeError` out of every command that read it — a traceback,
    exit 1 (the HAZARD code), outside the published 0/1/2/3 set (#8773 review P1).
    """
    repo.dirty(repo.a, "f.txt", "base\nlatin\n")
    cp = repo.git_bytes(b"stash", b"push", b"-q", b"-m", b"caf\xe9 work", cwd=repo.a)
    assert cp.returncode == 0, cp.stderr
    assert repo.stash_shas(), "precondition: the entry is in the register"

    listed = repo.lane(repo.a, "list")
    assert listed.returncode == 0, _fail(listed)
    assert "Traceback" not in listed.stderr, listed.stderr
    checked = repo.lane(repo.a, "check")
    assert checked.returncode in (0, 1), _fail(checked)
    assert "Traceback" not in checked.stderr, checked.stderr


def test_push_survives_a_peer_entry_with_a_non_utf8_message(repo):
    """A peer's non-UTF-8 subject must not crash `push` AFTER it stashed.

    `push` reads the whole global reflog to locate its own entry. A strict decode
    crashed there with the stash already created and NO lane pin — the unpinned
    live work `_pin_or_report` exists to prevent (#8773 review P1).
    """
    repo.dirty(repo.b, "f.txt", "base\npeer latin\n")
    cp = repo.git_bytes(b"stash", b"push", b"-q", b"-m", b"caf\xe9 peer", cwd=repo.b)
    assert cp.returncode == 0, cp.stderr
    raw = repo.git_bytes(
        b"log", b"--walk-reflogs", b"--format=%gs", b"-n", b"1", b"refs/stash",
        cwd=repo.b,
    )
    assert b"\xe9" in raw.stdout, (
        "precondition: git must keep the non-UTF-8 byte, else this test is vacuous"
    )
    repo.dirty(repo.a, "f.txt", "base\na\n")

    pushed = repo.lane(repo.a, "push", "-m", "A work")
    assert pushed.returncode == 0, _fail(pushed)
    assert "Traceback" not in pushed.stderr, pushed.stderr
    assert repo.stash_pins(), "push must pin its entry beside a non-UTF-8 peer subject"
