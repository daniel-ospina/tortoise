"""#10812 — a lane's UNCOMMITTED work must survive its worktree being destroyed.

Hermetic: a real temp git repo with a real linked worktree, real `git worktree
remove --force`, no network, no Docker, no FalkorDB. Nothing is monkeypatched —
the property under test IS git's behaviour plus the tool's contract around it.

The load-bearing test is :func:`test_pin_survives_a_forced_worktree_removal_and_
recover_restores_it`: pin a dirty worktree, destroy the worktree the way lane
`p1-10538` was destroyed, recover — the ~4,500 uncommitted lines come back
byte-identical, DELETIONS included.

:func:`test_premise_a_forced_removal_destroys_uncommitted_work` is the CONTROL. It
drives the same scenario with NO pin and asserts the loss still happens, so the
premise #10812 rests on ("the content was never in the object DB") is measured
rather than assumed.

SCOPE OF THAT CONTROL, stated precisely because the narrower claim is the true one:
it asserts the content is reachable from NO ref and no commit — i.e. git's own
history cannot give it back. It does NOT assert the bytes are gone from the
filesystem, so a future git that preserved them by some OTHER mechanism (archiving
the tree aside, a rescue copy under `.git/worktrees/`) would leave this control
passing. Verified by mutation: replacing the removal with "copy the dirty tree to a
rescue directory, then remove" keeps the control green. So the tool's reason to
exist is asserted against *git's recovery routes*, not against every possible
preservation mechanism.

Run:
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_lane_wip.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "lane_wip.py"
PYTHON = sys.executable

#: The #10812 shape: enough lines that "recoverable" is not a coincidence.
BIG = "\n".join(f"line {i}" for i in range(4500)) + "\n"


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def run_tool(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, str(TOOL), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def pin_ref(stdout: str) -> str:
    """The pin ref a successful `pin` printed — the handle a lane recovers with."""
    return next(ln.split()[1] for ln in stdout.splitlines() if ln.strip().startswith("ref"))


@pytest.fixture()
def hub(tmp_path: Path) -> Path:
    """A real repo with one commit; linked worktrees are added per test."""
    hub = tmp_path / "hub"
    hub.mkdir()
    git("init", "-q", ".", cwd=hub)
    git("config", "user.email", "t@example.com", cwd=hub)
    git("config", "user.name", "t", cwd=hub)
    (hub / "tracked.txt").write_text("v1\n")
    (hub / "doomed.txt").write_text("present at base, deleted by the lane\n")
    (hub / ".gitignore").write_text("ignored/\n")
    git("add", "-A", cwd=hub)
    git("commit", "-qm", "base", cwd=hub)
    return hub


def dirty_lane(hub: Path, name: str = "wt-lane") -> Path:
    """A linked worktree carrying the three kinds of uncommitted change."""
    wt = hub.parent / name
    made = git("worktree", "add", "-q", str(wt), "-b", f"lane-{name}", cwd=hub)
    assert made.returncode == 0, made.stderr
    (wt / "tracked.txt").write_text("v2 — the lane's uncommitted work\n")
    (wt / "big.py").write_text(BIG)
    (wt / "doomed.txt").unlink()
    (wt / "ignored").mkdir()
    (wt / "ignored" / "noise.bin").write_text("ignored, not work\n")
    return wt


def clean_lane(hub: Path, name: str = "wt-idxonly") -> Path:
    """A linked worktree whose worktree tree AND index both match HEAD."""
    wt = hub.parent / name
    made = git("worktree", "add", "-q", str(wt), "-b", f"lane-{name}", cwd=hub)
    assert made.returncode == 0, made.stderr
    return wt


def destroy(hub: Path, wt: Path, *, force: bool = True) -> subprocess.CompletedProcess:
    """The remover that cost #10812 ~4,500 lines: silent, rc=0, irrecoverable."""
    args = ["worktree", "remove"] + (["--force"] if force else []) + [str(wt)]
    return git(*args, cwd=hub)


# --------------------------------------------------------------- premise / control


def test_premise_a_forced_removal_destroys_uncommitted_work(hub: Path) -> None:
    """CONTROL — the loss #10812 describes still reproduces, with no pin involved.

    This is the measurement the fix is judged against: if this stopped failing to
    recover, the tool would be protecting against nothing.
    """
    wt = dirty_lane(hub)
    removed = destroy(hub, wt)
    assert removed.returncode == 0, removed.stderr
    assert not wt.exists(), "the worktree should be gone"

    # The branch was never moved, so the work was never committed...
    tip = git("ls-tree", "-r", "--name-only", "lane-wt-lane", cwd=hub).stdout.split()
    assert "big.py" not in tip
    assert "tracked.txt" in tip
    # ...and nothing in the object DB holds it.
    assert git("cat-file", "-e", "lane-wt-lane:big.py", cwd=hub).returncode != 0
    assert git("log", "--all", "--oneline", "--", "big.py", cwd=hub).stdout == ""


# ------------------------------------------------------------------------- the fix


def test_pin_survives_a_forced_worktree_removal_and_recover_restores_it(hub: Path) -> None:
    """THE FIX: pinned work survives the same removal, byte-identical.

    Modifications, untracked additions AND a deletion all come back, and the
    recovered tree's `git status` matches the lane's original status — so a reader
    cannot tell recovery from never having lost it.
    """
    wt = dirty_lane(hub)
    status_before = git("status", "--porcelain", cwd=wt).stdout

    pinned = run_tool("pin", "--note", "before the reviewer runs", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    # The count is not asserted: the snapshot is the WORKING STATE, so a `.gitignore`
    # inherited from HEAD rides along. Fidelity is asserted below, on content.
    assert "pinned" in pinned.stdout, pinned.stdout
    ref = pin_ref(pinned.stdout)
    assert ref.startswith("refs/pi-lane-wip/")

    removed = destroy(hub, wt)
    assert removed.returncode == 0 and not wt.exists()
    # The work is NOT on the branch — the pin is the only handle.
    assert git("cat-file", "-e", "lane-wt-lane:big.py", cwd=hub).returncode != 0

    recovered = run_tool(
        "recover",
        ref,
        "--into",
        str(wt),
        "--branch",
        "lane-wt-lane",
        cwd=hub,
    )
    assert recovered.returncode == 0, recovered.stderr

    assert (wt / "big.py").read_text() == BIG
    assert (wt / "tracked.txt").read_text() == "v2 — the lane's uncommitted work\n"
    assert not (wt / "doomed.txt").exists(), "the lane's deletion must be restored too"
    assert not (wt / "ignored").exists(), "ignored files are not work and are not pinned"
    assert git("status", "--porcelain", cwd=wt).stdout == status_before


def test_pin_is_zero_disruption(hub: Path) -> None:
    """A pin must not move HEAD, rewrite the index, or touch the working tree.

    Zero-disruption is the whole reason this is not `git stash push`: a lane pins
    to make its work SAFE, not to stop working.
    """
    wt = dirty_lane(hub)
    status_before = git("status", "--porcelain", cwd=wt).stdout
    head_before = git("rev-parse", "HEAD", cwd=wt).stdout
    index_before = (hub / ".git" / "worktrees" / "wt-lane" / "index").read_bytes()

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr

    assert git("status", "--porcelain", cwd=wt).stdout == status_before
    assert git("rev-parse", "HEAD", cwd=wt).stdout == head_before
    assert (hub / ".git" / "worktrees" / "wt-lane" / "index").read_bytes() == index_before
    assert (wt / "big.py").read_text() == BIG, "the lane keeps working on its own file"
    assert not (wt / "doomed.txt").exists()


def test_pin_reports_a_clean_tree_and_creates_no_ref(hub: Path) -> None:
    """A clean worktree has nothing to lose — say so instead of minting a ref."""
    wt = hub.parent / "wt-clean"
    assert git("worktree", "add", "-q", str(wt), "-b", "lane-clean", cwd=hub).returncode == 0

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    assert "clean" in pinned.stdout
    refs = git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub)
    assert refs.stdout.strip() == ""


def test_list_says_a_lane_has_no_pin_rather_than_nothing(hub: Path) -> None:
    """An empty list must name the lane and the remedy, never print nothing."""
    wt = dirty_lane(hub)
    listed = run_tool("list", cwd=wt)
    assert listed.returncode == 0, listed.stderr
    assert "has no wip pin" in listed.stdout
    assert "lane_wip.py pin" in listed.stdout


def test_list_shows_the_pin_after_pinning(hub: Path) -> None:
    wt = dirty_lane(hub)
    assert run_tool("pin", cwd=wt).returncode == 0
    listed = run_tool("list", cwd=wt)
    assert listed.returncode == 0, listed.stderr
    assert "refs/pi-lane-wip/" in listed.stdout
    assert "file(s)" in listed.stdout
    assert "lane-wip:" in listed.stdout


def test_pinning_a_worktree_that_is_gone_fails_loudly(hub: Path) -> None:
    """A vanished worktree must be refused, never silently swapped for another.

    This is the state a lane is in when it FINALLY notices the loss. A tool that
    quietly pinned the caller's cwd instead would answer "pinned" — telling the
    lane its work is safe when there was nothing left to save.
    """
    wt = dirty_lane(hub)
    assert destroy(hub, wt).returncode == 0
    assert not wt.exists()

    late = run_tool("--path", str(wt), "pin", cwd=hub)
    assert late.returncode == 2, late.stdout + late.stderr
    assert "not a directory" in late.stderr
    # And nothing was minted under the dead worktree's own lane key.
    assert git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub).stdout == ""


def test_pin_accepts_path_after_the_subcommand(hub: Path) -> None:
    """The advice this tool PRINTS must run: `<tool> pin --path <dir>`.

    `--path` declared only globally parses before the subcommand, so the printed
    remedy was rejected with "unrecognized arguments" — the one code path whose job
    is to hand a human the remedy printed one that did not work.
    """
    wt = dirty_lane(hub)
    after = run_tool("pin", "--path", str(wt), cwd=hub)
    assert after.returncode == 0, after.stdout + after.stderr
    assert "pinned" in after.stdout

    # ...and the global spelling still works (SUPPRESS keeps the two from clobbering).
    (wt / "big.py").write_text(BIG + "more\n")
    before = run_tool("--path", str(wt), "pin", cwd=hub)
    assert before.returncode == 0, before.stdout + before.stderr
    assert "pinned" in before.stdout


def test_the_note_is_persisted_and_listed(hub: Path) -> None:
    """`--note` must survive: git keeps NO reflog for a custom ref namespace.

    A pin's note was written only as an `update-ref -m` message, which git silently
    discards for `refs/pi-lane-wip/*` (no `.git/logs/refs/` entry at all) — so the
    pin was self-describing only in its own help text. The commit is the durable
    carrier; this pins that the note and the branch are actually in it.
    """
    wt = dirty_lane(hub)
    pinned = run_tool("pin", "--note", "before the reviewer runs", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    assert "before the reviewer runs" in pinned.stdout

    listed = run_tool("list", cwd=wt)
    assert listed.returncode == 0, listed.stderr
    assert "before the reviewer runs" in listed.stdout, listed.stdout
    assert "on lane-wt-lane" in listed.stdout, listed.stdout


# ---------------------------------------------------------------- the safety gate


def test_recover_refuses_a_dirty_target(hub: Path) -> None:
    """Recovery must not eat the work it was asked to save."""
    wt = dirty_lane(hub)
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    # A second lane with its own live uncommitted work, at the recovery target.
    other = hub.parent / "wt-other"
    assert git("worktree", "add", "-q", str(other), "-b", "lane-other", cwd=hub).returncode == 0
    (other / "live.txt").write_text("work that must not be eaten\n")

    refused = run_tool("recover", ref, "--into", str(other), cwd=hub)
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "DISCARD" in refused.stderr
    assert (other / "live.txt").read_text() == "work that must not be eaten\n"

    forced = run_tool("recover", ref, "--into", str(other), "--force", cwd=hub)
    assert forced.returncode == 0, forced.stderr


def test_recover_ignores_a_sha_that_names_no_commit(hub: Path) -> None:
    bogus = run_tool("recover", "refs/pi-lane-wip/nope/1", cwd=hub)
    assert bogus.returncode == 2
    assert "does not name a commit" in bogus.stderr


# ----------------------------------------------------------------- the namespace


def test_pins_live_outside_refs_lane(hub: Path) -> None:
    """A wip pin must NOT be visible to `lane_stash`'s ownership reasoning.

    `lane_stash._lane_key_of_ref` treats ANY `refs/lane/*` ref as lane-owned, so a
    wip pin under it would change what `lane_stash` believes about stashes. This
    pins the namespace decision so a future "tidy-up" cannot move it silently.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import lane_stash

    wt = dirty_lane(hub)
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    assert ref.startswith("refs/pi-lane-wip/")
    assert not ref.startswith("refs/lane/")
    assert lane_stash._lane_key_of_ref(ref) is None, "lane_stash would claim ownership of a wip pin"


def test_two_pins_of_one_lane_get_distinct_refs(hub: Path) -> None:
    """A second pin must never overwrite the first — the loser is lost work."""
    wt = dirty_lane(hub)
    first = run_tool("pin", cwd=wt)
    assert first.returncode == 0, first.stderr
    (wt / "big.py").write_text(BIG + "more work\n")
    second = run_tool("pin", cwd=wt)
    assert second.returncode == 0, second.stderr

    refs = [
        ln.strip()
        for ln in git(
            "for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub
        ).stdout.splitlines()
        if ln.strip()
    ]
    assert len(refs) == 2, refs
    assert refs[0] != refs[1]


def test_recipe_prints_the_recovery_steps(hub: Path) -> None:
    """#10812's third ask: the recovery path must be documented, not folklore."""
    out = run_tool("recipe", cwd=hub)
    assert out.returncode == 0, out.stderr
    assert "recover" in out.stdout
    assert "lane_wip.py pin" in out.stdout


def test_pinning_a_non_repo_directory_is_a_state_error(tmp_path: Path) -> None:
    """A wrong `--path` must say so, not leak git's `fatal:` with exit 3.

    `is_dir()` alone let `--path /tmp` reach a `rev-parse` and surface
    `fatal: not a git repository` — a message that names neither the caller's
    mistake nor the remedy, and the wrong exit code for a usage error.
    """
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    out = run_tool("pin", "--path", str(plain), cwd=plain)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "not a git work tree" in out.stderr
    assert "fatal:" not in out.stderr


def test_list_all_finds_pins_from_every_lane(hub: Path) -> None:
    """`list --all` is RECIPE step 1 — an empty answer says "the work is gone".

    `for-each-ref` matches with `WM_PATHNAME`, so `refs/pi-lane-wip/*` does NOT
    cross `/`, and pins live two components deep (`<key>/<n>`) — the glob form
    matched nothing while the refs sat there, telling the disaster victim its pinned
    work was gone.
    """
    first = dirty_lane(hub, "wt-alpha")
    second = hub.parent / "wt-beta"
    assert git("worktree", "add", "-q", str(second), "-b", "lane-beta", cwd=hub).returncode == 0
    (second / "beta.py").write_text("beta work\n")

    assert run_tool("pin", cwd=first).returncode == 0
    assert run_tool("pin", cwd=second).returncode == 0

    listed = run_tool("list", "--all", cwd=hub)
    assert listed.returncode == 0, listed.stderr
    assert "no lane has any wip pin" not in listed.stdout
    assert "wt-alpha" in listed.stdout, listed.stdout
    assert "wt-beta" in listed.stdout, listed.stdout
    assert listed.stdout.count("refs/pi-lane-wip/") >= 2, listed.stdout


def test_pin_writes_the_ref_into_the_path_repo_not_the_caller(hub: Path) -> None:
    """`pin --path <other repo>` must mint the ref in THAT repo.

    `update-ref` resolves the ref and looks up the object in the repo it runs in, so
    omitting `cwd` attempted the write in the caller's checkout and answered
    `nonexistent object` — no ref anywhere, exit 3. This is the remedy `recover`
    prints, so it has to run.
    """
    other_hub = hub.parent / "other-hub"
    other_hub.mkdir()
    assert git("init", "-q", ".", cwd=other_hub).returncode == 0
    git("config", "user.email", "t@example.com", cwd=other_hub)
    git("config", "user.name", "t", cwd=other_hub)
    (other_hub / "f.txt").write_text("base\n")
    git("add", "-A", cwd=other_hub)
    assert git("commit", "-qm", "base", cwd=other_hub).returncode == 0
    (other_hub / "mine.py").write_text("other repo's work\n")

    out = run_tool("pin", "--path", str(other_hub), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "pinned" in out.stdout

    refs_here = git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub)
    assert refs_here.stdout.strip() == "", "must not write a ref into the caller's repo"
    refs_there = git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=other_hub)
    assert refs_there.stdout.strip().startswith("refs/pi-lane-wip/"), refs_there.stdout


def test_a_note_that_starts_with_the_trailer_words_survives(hub: Path) -> None:
    """The tool's own trailer must be stripped by POSITION, never by prefix.

    A caller's note is free to begin with "pinned at …"; filtering on that prefix
    silently dropped the note and kept the boilerplate instead.

    ⛔ AND THE SAME IS TRUE OF THE NOTE'S **LAST** LINE, which is the only line the
    strip can actually reach. `snapshot` separates the note from its trailer with a
    blank line, so what the trailer loop sees after popping `pinned at <ts>` is that
    blank — never the caller's text. That is a property of the WRITER, not of the
    strip, so it is pinned here for every trailer word the reader has ever stripped
    (a former `index-tree ` entry was removed as unreachable; a future one that
    appends after the trailer instead of before it would break this). The
    second case is the one an earlier version of this file got wrong in its
    COMMENT — it claimed to strip `index-tree` "before" `pinned at` while the loop
    could never reach a caller's line — so the assertion is on the last line.
    """
    for i, note in enumerate(("pinned at noon, why not", "first line\nindex-tree deadbeef")):
        # ⛔ A DETERMINISTIC name, like every other `dirty_lane` caller in this file
        # (`wt-forge`, `wt-lane`, `wt-idxonly`). `hash()` is salted per interpreter,
        # so deriving the name from the note made the two iterations collide with
        # p ~ 1/10000 — and a collision reds a merge-gating test for no reason at
        # `dirty_lane`'s `assert made.returncode == 0` (the branch already exists).
        wt = dirty_lane(hub, f"wt-note-{i}")
        pinned = run_tool("pin", "--note", note, cwd=wt)
        assert pinned.returncode == 0, pinned.stderr
        listed = run_tool("list", cwd=wt)
        last = note.splitlines()[-1]
        assert last in listed.stdout, (
            f"the note's LAST line {last!r} must survive the trailer strip: {listed.stdout}"
        )


def test_list_reports_a_damaged_pin_rather_than_zero_files(hub: Path) -> None:
    """A pin whose TREE is unreadable must not list as `0 file(s)`.

    `_out(..., check=False)` yields "" for any failure, so a pin whose commit
    survives but whose tree object is gone (a damaged or partially-pruned object DB
    — the disaster case this tool exists for) printed `0 file(s)` with rc 0 and no
    warning, i.e. "this pin holds nothing". The count is UNKNOWN, not zero.
    """
    wt = dirty_lane(hub)
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    sha = next(ln.split()[1] for ln in pinned.stdout.splitlines() if ln.strip().startswith("sha"))
    tree = git("rev-parse", f"{sha}^{{tree}}", cwd=hub).stdout.strip()

    # Destroy exactly the tree object, leaving the commit reachable: the state a
    # partial clone / pruned object DB leaves behind.
    obj = hub / ".git" / "objects" / tree[:2] / tree[2:]
    assert obj.exists(), obj
    obj.unlink()
    assert git("cat-file", "-e", f"{sha}^{{tree}}", cwd=hub).returncode != 0

    listed = run_tool("list", cwd=wt)
    assert listed.returncode == 0, listed.stderr
    assert "0 file(s)" not in listed.stdout, listed.stdout
    assert "DAMAGED" in listed.stdout, listed.stdout
    assert "UNKNOWN" in listed.stdout, listed.stdout
    assert sha[:12] in listed.stdout, "the pin is still named, so it can be triaged"


def test_a_tmpdir_inside_the_worktree_is_never_snapshotted(hub: Path) -> None:
    """The scratch index must be provably OUTSIDE the worktree.

    `tempfile` honours TMPDIR, so an inherited `TMPDIR=<worktree>/tmp` put the scratch
    index INSIDE the tree and `add -A` snapshotted the tool's own index into the pin —
    a pin carrying its own scratch state, which is exactly what must not happen to the
    artifact that has to survive.
    """
    wt = dirty_lane(hub)
    scratch_parent = wt / "tmpdir"
    scratch_parent.mkdir()
    pinned = subprocess.run(
        [PYTHON, str(TOOL), "pin"],
        cwd=str(wt),
        capture_output=True,
        text=True,
        env={**os.environ, "TMPDIR": str(scratch_parent)},
    )
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    sha = next(ln.split()[1] for ln in pinned.stdout.splitlines() if ln.strip().startswith("sha"))
    files = git("ls-tree", "-r", "--name-only", sha, cwd=hub).stdout.split("\n")
    assert not [f for f in files if "lane-wip-index" in f], files
    assert not [f for f in files if f.startswith("tmpdir/")], files
    assert "big.py" in files and "tracked.txt" in files, files


def test_recover_into_an_existing_non_repo_directory_is_a_state_error(hub: Path) -> None:
    """A recreated-by-`mkdir` path must be refused with the remedy, not a git fatal.

    Measured before the fix: `recover --into <mkdir'd path>` exited 3 with
    `fatal: not a git repository` and restored nothing — on the one path whose whole
    job is to hand a lane its work back.
    """
    wt = dirty_lane(hub)
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    wt.mkdir()  # a lane recreating its old path by hand
    out = run_tool("recover", ref, "--into", str(wt), cwd=hub)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "not a git work tree" in out.stderr
    assert "fatal:" not in out.stderr
    assert "rmdir" in out.stderr


def test_recover_into_an_unborn_head_worktree_restores_as_uncommitted(hub: Path) -> None:
    """Recovery resets to the pin's PARENT, so an orphan worktree is a valid target.

    The unstage step used to be `reset --mixed HEAD`, which fails on an unborn HEAD and
    was the one swallowed failure in a success path; resting it on the pin's parent
    both removes that failure mode and makes the restore correct in a tree with no
    history. The result must still be UNCOMMITTED work on top of that parent.
    """
    wt = dirty_lane(hub)
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    orphan = hub.parent / "wt-orphan"
    made = git("worktree", "add", "--orphan", "-b", "lane-orphan", str(orphan), cwd=hub)
    assert made.returncode == 0, made.stderr
    assert git("rev-parse", "--verify", "HEAD", cwd=orphan).returncode != 0, "no HEAD"

    out = run_tool("recover", ref, "--into", str(orphan), "--force", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "recovered" in out.stdout
    # Uncommitted on top of the parent: the content is back AND HEAD is not the snapshot.
    assert (orphan / "big.py").read_text() == BIG
    assert not (orphan / "doomed.txt").exists()
    assert (
        git("rev-parse", "HEAD", cwd=orphan).stdout.strip()
        != git("rev-parse", ref, cwd=hub).stdout.strip()
    ), "HEAD must be the pin's parent, never the snapshot itself"


def test_a_staged_only_change_is_pinned_not_called_clean(hub: Path) -> None:
    """A `MM`/`AD` tree holds work the WORKTREE does not show — it must not read "clean".

    `read-tree HEAD` + `add -A` sees only the working tree, so a staged edit whose
    working file was reverted (`MM`) and a staged new file later deleted (`AD`) both
    compared equal to HEAD and `pin` printed "clean — nothing to pin" with rc 0, minting
    no ref — a false "nothing to save" over a staged blob that existed nowhere else.
    """
    # ⛔ THE WORKTREE MUST BE GENUINELY CLEAN, or this test does not test the index
    # half at all: started from `dirty_lane()` the worktree tree already differs from
    # HEAD (an untracked file, a deletion), so "clean" was refused for the WORKTREE
    # reason and a broken index half survived the whole suite. Measured: mutating the
    # guard to accept a staged-only tree as clean kept 36/36 green.
    wt = clean_lane(hub)
    assert git("status", "--porcelain", cwd=wt).stdout.strip() == "", (
        "the worktree must start clean"
    )
    # `MM`: staged edit, worktree reverted to HEAD.
    (wt / "tracked.txt").write_text("v2 staged\n")
    git("add", "tracked.txt", cwd=wt)
    (wt / "tracked.txt").write_text("v1\n")
    assert git("status", "--porcelain", "tracked.txt", cwd=wt).stdout.startswith("MM")
    # `AD`: staged new file, deleted from the worktree.
    (wt / "only-in-index.py").write_text("staged only\n")
    git("add", "only-in-index.py", cwd=wt)
    (wt / "only-in-index.py").unlink()
    assert git("status", "--porcelain", "only-in-index.py", cwd=wt).stdout.startswith("AD")

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    assert "nothing to pin" not in pinned.stdout, pinned.stdout
    ref = pin_ref(pinned.stdout)
    assert ref.startswith("refs/pi-lane-wip/")
    assert "index" in pinned.stdout, "the pinned staged tree must be reported"

    # The staged blob is recoverable from the pin even though the worktree lost it.
    index_tree = next(
        ln.split()[1] for ln in pinned.stdout.splitlines() if ln.strip().startswith("index")
    )
    files = git("ls-tree", "-r", "--name-only", index_tree, cwd=hub).stdout.split()
    assert "only-in-index.py" in files, files


def test_recover_restores_the_staged_unstaged_split(hub: Path) -> None:
    """A lane that had staged part of its work must get that split back.

    Everything was unstaged on recovery, so `diff --cached` came back empty and the
    lane's next bare `git commit` — which would have committed the staged part —
    committed nothing: a silent no-op on recovered work.
    """
    wt = dirty_lane(hub)
    (wt / "staged.txt").write_text("staged content\n")
    git("add", "staged.txt", cwd=wt)  # staged, not committed
    (wt / "unstaged.py").write_text("unstaged\n")  # never added
    before = git("status", "--porcelain", cwd=wt).stdout
    before_cached = git("diff", "--cached", "--name-only", cwd=wt).stdout.split()

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = run_tool("recover", ref, "--into", str(wt), "--branch", "lane-wt-lane", cwd=hub)
    assert out.returncode == 0, out.stderr
    assert git("status", "--porcelain", cwd=wt).stdout == before
    assert git("diff", "--cached", "--name-only", cwd=wt).stdout.split() == before_cached
    assert (wt / "staged.txt").read_text() == "staged content\n"
    assert (wt / "unstaged.py").read_text() == "unstaged\n"


def test_recover_without_branch_leaves_the_work_uncommitted(hub: Path) -> None:
    """Detaching at the SNAPSHOT made the restore a no-op and reported a clean tree.

    `worktree add --detach <target> <sha>` puts HEAD at the snapshot, so `read-tree`
    changed nothing and the lane could not tell its work had come back: `git status`
    was clean and `git commit` said "nothing to commit".
    """
    wt = dirty_lane(hub)
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = run_tool("recover", ref, "--into", str(wt), cwd=hub)
    assert out.returncode == 0, out.stderr
    status = git("status", "--porcelain", cwd=wt).stdout
    assert status.strip(), "the recovered work must be VISIBLE as uncommitted changes"
    assert "big.py" in status, status
    snapshot_sha = git("rev-parse", ref, cwd=hub).stdout.strip()
    assert git("rev-parse", "HEAD", cwd=wt).stdout.strip() != snapshot_sha


def test_recover_refuses_to_overwrite_an_ignored_file(hub: Path) -> None:
    """Ignored files are local state too — restoring must not silently replace them.

    `status --porcelain` does not see ignored files, so a target whose only content was
    a local config was judged CLEAN and the restore replaced it: exit 0 and a local
    secret gone. This repo's own teardown guard treats ignored-file destruction as
    destroy-worthy for the same reason.
    """
    wt = dirty_lane(hub)
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    target = hub.parent / "wt-ignored"
    assert git("worktree", "add", "-q", str(target), "-b", "lane-ign", cwd=hub).returncode == 0
    (target / "config.cfg").write_text("LOCAL SECRET, IGNORED\n")  # matches .gitignore? no:
    git("check-ignore", "-q", "config.cfg", cwd=target)  # informational
    (hub / ".git" / "info").mkdir(exist_ok=True)
    with open(hub / ".git" / "info" / "exclude", "a") as fh:
        fh.write("config.cfg\n")
    # It is now ignored, so plain `status --porcelain` calls the tree clean.
    assert git("status", "--porcelain", cwd=target).stdout.strip() == ""
    assert "config.cfg" in git("status", "--porcelain", "--ignored=traditional", cwd=target).stdout

    refused = run_tool("recover", ref, "--into", str(target), cwd=hub)
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "DISCARD" in refused.stderr
    assert (target / "config.cfg").read_text() == "LOCAL SECRET, IGNORED\n"


def test_a_subdirectory_is_not_a_lane(hub: Path) -> None:
    """`--path <worktree>/src` must key on the WORKTREE, not the subdirectory.

    Keying on the subdirectory minted a different lane key, so `list` at the worktree
    root answered "has no wip pin" — the false "the work is gone" verdict the recovery
    recipe must never give.
    """
    wt = dirty_lane(hub)
    sub = wt / "pkg"
    sub.mkdir()
    (sub / "mod.py").write_text("sub work\n")

    pinned = run_tool("pin", "--path", str(sub), cwd=hub)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    # Keyed on the worktree: `list` AT THE ROOT must see it.
    listed = run_tool("list", cwd=wt)
    assert listed.returncode == 0, listed.stderr
    assert "has no wip pin" not in listed.stdout, listed.stdout
    assert "refs/pi-lane-wip/" in listed.stdout


def test_recover_refuses_a_pin_older_than_the_target_head(hub: Path) -> None:
    """A stale pin must not revert commits made since it was taken.

    The target is CLEAN, so the dirty guard cannot fire — and a whole-tree `read-tree`
    of an older snapshot deleted a file committed after the pin and reverted another,
    printing success with rc 0.
    """
    wt = dirty_lane(hub)
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    # The lane commits EVERYTHING after pinning, so the target is CLEAN at a new HEAD
    # — which is exactly the state the dirty guard cannot see.
    (wt / "committed_later.py").write_text("landed work\n")
    # The fixture ships an ignored directory; the dirty guard counts ignored files
    # (by design), so drop it to reach a genuinely clean target.
    shutil.rmtree(wt / "ignored", ignore_errors=True)
    assert git("add", "-A", cwd=wt).returncode == 0
    assert git("commit", "-qm", "later work", cwd=wt).returncode == 0
    assert git("status", "--porcelain", "--ignored=traditional", cwd=wt).stdout.strip() == "", (
        "the target must be clean by the SAME test the guard applies"
    )

    refused = run_tool("recover", ref, "--into", str(wt), cwd=hub)
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "pinned on" in refused.stderr
    assert "LOSE work" in refused.stderr
    assert (wt / "committed_later.py").read_text() == "landed work\n"


def test_pin_order_past_nine_is_numeric(hub: Path) -> None:
    """`| tail -1` must give the NEWEST pin past /9 — lexicographically /10 < /9."""
    wt = dirty_lane(hub)
    for i in range(11):
        (wt / "big.py").write_text(BIG + f"edit {i}\n")
        out = run_tool("pin", cwd=wt)
        assert out.returncode == 0, out.stderr
    listed = run_tool("list", cwd=wt)
    assert listed.returncode == 0, listed.stderr
    rows = [ln for ln in listed.stdout.splitlines() if ln.startswith("refs/")]
    assert len(rows) == 11, rows
    assert rows[-1].split()[0].endswith("/11"), rows[-1]
    assert rows[0].split()[0].endswith("/1"), rows[0]


# ---------------------------------------------------------------- durability and the guards
# The tests below exist because MUTATION TESTING found them absent: replacing
# `if unstaged.returncode != 0:` / `if staged.returncode != 0:` / `if cp.returncode != 0:`
# with `if False:` left the suite 29/29 GREEN. Each is a guard a review found and fixed,
# so without these a regression re-opens a silent-success path with a green suite.


def test_the_pinned_index_tree_survives_gc_and_still_restores_the_split(hub: Path) -> None:
    """The staged view must be a REACHABILITY ROOT, not a note in a commit message.

    As a commit-message trailer the index tree was collected by `git gc --prune=now` (a
    message is not a root), so a pin whose staged half held work nothing else held — an
    `AD` blob exists ONLY in the index — came back short. Measured before the fix:
    `fatal: failed to unpack tree object`, `recover` exit 3, file gone.
    """
    wt = dirty_lane(hub, "wt-gc")
    # `AD`: staged-add then deleted from the worktree — the blob exists ONLY in the index.
    (wt / "only-in-index.py").write_text("staged, then removed from the worktree\n")
    assert git("add", "only-in-index.py", cwd=wt).returncode == 0
    (wt / "only-in-index.py").unlink()
    assert "AD only-in-index.py" in git("status", "--porcelain", cwd=wt).stdout

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    assert destroy(hub, wt).returncode == 0
    # The whole point: gc must be unable to take the pin.
    gc = git("gc", "--prune=now", "--quiet", cwd=hub)
    assert gc.returncode == 0, gc.stderr

    recovered = run_tool("recover", ref, "--into", str(wt), cwd=hub)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    status = git("status", "--porcelain", cwd=wt).stdout
    assert "AD only-in-index.py" in status, status
    cached = git("diff", "--cached", "--name-only", cwd=wt).stdout
    assert "only-in-index.py" in cached, (
        f"the staged half must survive a gc, not just the worktree half: {cached!r}"
    )


@pytest.fixture()
def git_shim(tmp_path: Path) -> Path:
    """A `git` that can be told to fail ONE plumbing call, and passes everything else."""
    real = shutil.which("git")
    assert real, "git must be on PATH"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    script = shim_dir / "git"
    # ⛔ `_git` puts `-C <cwd>` BEFORE the subcommand, so `$1` is `-C` — a shim that
    # dispatches on `$1` silently never fires, and a never-firing shim makes every guard
    # test pass for the wrong reason.
    # The two index-only calls are both plain `read-tree`, so they are told apart by
    # ORDER: the first (the unstage, onto the pin's parent) and the second (the staged
    # split, onto the pinned index tree).
    script.write_text(
        f"""#!/bin/sh
prefix=
if [ "$1" = "-C" ]; then prefix="$1 $2"; shift 2; fi
sub="$1"
case "$sub" in
  write-tree)
    # BOTH index reads carry GIT_INDEX_FILE now (the real-index capture renders from a
    # COPY, so a pin never takes index.lock), so they are told apart by the temp-dir name:
    # the worktree snapshot uses `lane-wip-index-*`, the real-index copy `lane-wip-rtree-*`.
    case "$GIT_INDEX_FILE" in
      *lane-wip-index-*)
        if [ -n "$LW_FAIL_SCRATCH_WRITE" ]; then
          echo "shim: refusing scratch write-tree" >&2; exit 128
        fi ;;
      *lane-wip-rtree-*)
        if [ -n "$LW_FAIL_REAL_WRITE" ]; then
          echo "shim: refusing real write-tree" >&2; exit 128
        fi
        # A FAILING GIT THAT SAYS NOTHING: the only shape that exercises the fallback
        # reason, and the only one a stderr-based guard cannot cover.
        if [ -n "$LW_FAIL_REAL_WRITE_SILENT" ]; then
          exit 128
        fi ;;
    esac ;;
  add)
    [ -n "$LW_FAIL_ADD" ] && {{ echo "shim: refusing add" >&2; exit 128; }} ;;
  update-index)
    [ -n "$LW_FAIL_UPDATE_INDEX" ] && {{ echo "shim: refusing update-index" >&2; exit 128; }} ;;
  for-each-ref)
    [ -n "$LW_FAIL_FOR_EACH_REF" ] && {{ echo "shim: refusing for-each-ref" >&2; exit 128; }} ;;
  diff-tree)
    [ -n "$LW_FAIL_DIFF_TREE" ] && {{ echo "shim: refusing diff-tree" >&2; exit 128; }} ;;
  update-ref)
    [ -n "$LW_FAIL_UPDATE_REF" ] && {{ echo "shim: cannot lock ref" >&2; exit 128; }} ;;
  worktree)
    if [ -n "$LW_DETACH_WORKTREE_ADD" ] && [ "$2" = "add" ]; then
      first="$1"; second="$2"; shift 2
      exec {real} $prefix "$first" "$second" --detach "$@"
    fi ;;
  read-tree)
    has_reset=
    for a in "$@"; do [ "$a" = "--reset" ] && has_reset=1; done
    if [ -n "$has_reset" ]; then
      [ -n "$LW_FAIL_RESTORE" ] && {{ echo "shim: refusing read-tree --reset" >&2; exit 128; }}
    else
      here=$(dirname "$0")
      n=$(cat "$here/plain-count" 2>/dev/null || echo 0)
      n=$((n + 1))
      echo "$n" > "$here/plain-count"
      if [ "$n" = "1" ]; then
        [ -n "$LW_FAIL_UNSTAGE" ] && {{ echo "shim: refusing read-tree (unstage)" >&2; exit 128; }}
      else
        [ -n "$LW_FAIL_STAGED" ] && {{ echo "shim: refusing read-tree (staged)" >&2; exit 128; }}
      fi
    fi ;;
esac
exec {real} $prefix "$@"
"""
    )
    script.chmod(0o755)
    return shim_dir


def _run_tool_with_shim(shim: Path, env_key: str, *args: str, cwd: Path):
    env = dict(os.environ)
    env["PATH"] = f"{shim}{os.pathsep}{env.get('PATH', '')}"
    env[env_key] = "1"
    return subprocess.run(
        [PYTHON, str(TOOL), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_a_failed_unstage_is_reported_not_swallowed(hub: Path, git_shim: Path) -> None:
    """`git reset --mixed` failing must NOT produce `recovered N file(s)` and exit 0.

    Swallowed, the snapshot stayed STAGED while the tool claimed success — the "the
    lane's next bare commit does what it would have" contract, silently unmet.
    """
    wt = dirty_lane(hub, "wt-unstage")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = _run_tool_with_shim(
        git_shim, "LW_FAIL_UNSTAGE", "recover", ref, "--into", str(wt), cwd=hub
    )
    assert out.returncode != 0, f"a failed unstage must not exit 0: {out.stdout}"
    assert "could not unstage" in out.stderr, out.stderr
    assert "recovered" not in out.stdout, "success must not be claimed when the unstage failed"


def test_a_failed_working_tree_restore_is_reported(hub: Path, git_shim: Path) -> None:
    """The main `read-tree --reset -u` failing must be an error, not a success message."""
    wt = dirty_lane(hub, "wt-restore")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = _run_tool_with_shim(
        git_shim, "LW_FAIL_RESTORE", "recover", ref, "--into", str(wt), cwd=hub
    )
    assert out.returncode != 0, out.stdout
    assert "could not restore" in out.stderr, out.stderr
    assert "recovered" not in out.stdout


def test_a_failed_staged_restore_is_reported(hub: Path, git_shim: Path) -> None:
    """Losing the split must fail loudly — it is the difference between a commit that
    lands the staged part and one that commits nothing."""
    wt = dirty_lane(hub, "wt-staged")
    (wt / "staged.py").write_text("staged\n")
    assert git("add", "staged.py", cwd=wt).returncode == 0
    (wt / "staged.py").write_text("staged, then edited\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    assert "index " in pinned.stdout, "a staged change must be captured"
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = _run_tool_with_shim(
        git_shim, "LW_FAIL_STAGED", "recover", ref, "--into", str(wt), cwd=hub
    )
    assert out.returncode != 0, out.stdout
    assert "could not restore its staged state" in out.stderr, out.stderr
    assert "split it was pinned with" not in out.stdout, "the split must not be claimed"


def test_a_note_cannot_forge_the_pinned_index(hub: Path) -> None:
    """A caller's `--note` must not steer `recover` at a DIFFERENT index.

    The index tree used to be an `index-tree <sha>` line that `recover` found by
    PREFIX-SCANNING the commit body, and a caller's note is written ABOVE that trailer —
    so a note could forge it. Measured before the fix: `recover` exited 0, printed "with
    the staged/unstaged split it was pinned with", and rebuilt an index from the forged
    tree. It is now the pin's SECOND PARENT, which no note can reach.

    Discrimination: the forged tree here is HEAD's, so if a note could steer recovery the
    staged half would come back EMPTY. It comes back staged — that is the proof.
    """
    wt = dirty_lane(hub, "wt-forge")
    (wt / "staged.py").write_text("staged\n")
    assert git("add", "staged.py", cwd=wt).returncode == 0
    (wt / "staged.py").write_text("staged, then edited\n")
    head_tree = git("rev-parse", "HEAD^{tree}", cwd=wt).stdout.strip()

    pinned = run_tool("pin", "--note", f"hello\nindex-tree {head_tree}\nworld", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    recorded = git("rev-parse", f"{ref}^2^{{tree}}", cwd=hub).stdout.strip()
    assert recorded, "the pinned index must be addressable as the pin's second parent"
    assert recorded != head_tree, "the pin recorded HEAD's tree, not the lane's index"

    assert destroy(hub, wt).returncode == 0
    out = run_tool("recover", ref, "--into", str(wt), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    cached = git("diff", "--cached", "--name-only", cwd=wt).stdout
    assert "staged.py" in cached, (
        f"the staged half must come back from the pin's index, not the forger's: {cached!r}"
    )


def test_recover_handles_a_worktree_removed_without_git(hub: Path) -> None:
    """`rm -rf` leaves git's admin record behind, and `worktree add` then refuses the path.

    That is the `cwd … (deleted)` shape #10812 is about — not `worktree remove`. Measured
    before the fix: `fatal: '<path>' is a missing but already registered worktree`, rc 3,
    directory never recreated, and no word that the pin was intact.
    """
    wt = dirty_lane(hub, "wt-rf")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    shutil.rmtree(wt)
    assert (hub / ".git" / "worktrees").exists(), "git's admin record must still be there"

    out = run_tool("recover", ref, "--into", str(wt), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert wt.exists(), "the worktree must be recreated"
    assert (wt / "big.py").read_text() == BIG


def test_recover_recreates_a_branch_the_reaper_took(hub: Path) -> None:
    """`pin` prints a recovery command naming the branch; the reaper may take it first.

    Measured before the fix: `fatal: invalid reference: lane-wt-gone`, work not restored,
    and the working alternative (omit `--branch`) mentioned nowhere.
    """
    wt = dirty_lane(hub, "wt-gone")
    branch = "lane-wt-gone"
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0
    assert git("branch", "-D", branch, cwd=hub).returncode == 0

    out = run_tool("recover", ref, "--into", str(wt), "--branch", branch, cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert git("rev-parse", "--verify", "-q", branch, cwd=hub).returncode == 0, (
        "the branch must be recreated at the pin's parent"
    )
    assert (wt / "big.py").read_text() == BIG


def test_recover_does_not_move_the_target_branch(hub: Path) -> None:
    """`recover` must never rewind a branch — that destroys committed work on exit 0.

    `git reset --mixed <commit>` moves HEAD, so the unstage step rewound the branch to
    the pin's parent. Measured before the fix: a lane pinned at `43e3e5c8`, work landed
    on the branch (tip `7f202164`), the worktree was removed, and the recovery command
    `pin` itself prints exited 0 having rewound the branch to `43e3e5c8` — after which
    the landed commit was on NO ref, and `reflog expire && gc --prune=now` destroyed it.
    """
    wt = dirty_lane(hub, "wt-move")
    branch = "lane-wt-move"
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    tip_before = git("rev-parse", branch, cwd=hub).stdout.strip()

    assert destroy(hub, wt).returncode == 0
    assert git("rev-parse", "--verify", "-q", branch, cwd=hub).returncode == 0, (
        "the branch outlives the worktree — that is the shape that had no guard"
    )

    out = run_tool("recover", ref, "--into", str(wt), "--branch", branch, cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert git("rev-parse", branch, cwd=hub).stdout.strip() == tip_before, (
        "the branch tip moved — committed work can be orphaned by a successful recovery"
    )
    assert (wt / "big.py").read_text() == BIG


def test_recover_refuses_a_branch_that_advanced_since_the_pin(hub: Path) -> None:
    """The absent-target path must carry the stale guard too, not just the existing one.

    Before the fix the guard lived only inside `if target.exists()`, so the shape `pin`
    prints — target gone, `--branch` naming a branch that survived — had no guard at
    all and mixed the pin's tree with the branch's newer commits.
    """
    wt = dirty_lane(hub, "wt-adv")
    branch = "lane-wt-adv"
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    # The lane commits after pinning: the branch advances past the pin's parent.
    (wt / "landed.py").write_text("reviewed and committed\n")
    assert git("add", "landed.py", cwd=wt).returncode == 0
    # Commit ONLY that path: a plain `git commit` would also commit the pin's staged
    # file, making the pin's staged blob identical to HEAD and the assertion below
    # vacuous (`git diff --cached` would be empty for the right reason, not the fixed one).
    assert git("commit", "-qm", "landed", "--", "landed.py", cwd=wt).returncode == 0
    advanced = git("rev-parse", branch, cwd=hub).stdout.strip()

    assert destroy(hub, wt).returncode == 0
    out = run_tool("recover", ref, "--into", str(wt), "--branch", branch, cwd=hub)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "was pinned on" in out.stderr, out.stderr

    # --force builds the pin's tree on that branch, and STILL must not move the branch.
    forced = run_tool("recover", ref, "--into", str(wt), "--branch", branch, "--force", cwd=hub)
    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert git("rev-parse", branch, cwd=hub).stdout.strip() == advanced, (
        "--force must not authorise a branch rewind"
    )
    assert git("cat-file", "-e", f"{advanced}^{{commit}}", cwd=hub).returncode == 0


def test_a_truncated_index_is_reported_not_pinned_silently(hub: Path) -> None:
    """`write-tree` exits 0 while printing `cache entry has null sha1` on a bad index.

    Keying on rc alone pinned a garbled index and announced "staged state is pinned too",
    so the pin claimed a second view it did not have. The warning must name the index.
    """
    wt = dirty_lane(hub, "wt-trunc")
    index = wt / ".git" / "index"
    if not index.exists():  # linked worktrees keep the index in the common dir
        index = Path(git("rev-parse", "--git-path", "index", cwd=wt).stdout.strip())
        if not index.is_absolute():
            index = (wt / index).resolve()
    data = index.read_bytes()
    assert len(data) > 64, data[:32]
    index.write_bytes(data[:64])

    pinned = run_tool("pin", cwd=wt)
    # It must never CLAIM a second view it does not have...
    assert "staged state is pinned too" not in pinned.stdout, pinned.stdout
    # ...and it must SAY the staged half is missing (or refuse outright).
    assert pinned.returncode != 0 or "could not be captured" in pinned.stderr, (
        f"a truncated index must be reported: rc={pinned.returncode} "
        f"stdout={pinned.stdout!r} stderr={pinned.stderr!r}"
    )
    if pinned.returncode == 0:
        assert "STAGED state is NOT" in pinned.stderr, pinned.stderr


def test_force_recovery_does_not_stage_a_revert_of_committed_work(hub: Path) -> None:
    """Unstaging onto the PIN'S PARENT stages a whole-tree revert; it must use the
    target's own HEAD.

    Measured before the fix: pin, commit `landed.py` (branch advanced), remove worktree,
    `recover --branch <b> --force` exited 0 with `git status` showing `D big.py` /
    `D landed.py` STAGED — so the lane's next bare commit (the exact action the module
    reasons about) deleted the pin's own 4,500-line file and a post-pin commit from the
    branch. In every non-forced shape HEAD *is* the parent, which is what hid it.
    """
    wt = dirty_lane(hub, "wt-norevert")
    branch = "lane-wt-norevert"
    # ⛔ THE PIN MUST CARRY A STAGED HALF. Built from `dirty_lane()` alone there is no
    # staged file, so `index_tree` was "" and the staged-restore line — the one that
    # staged the revert — never executed: the test passed with the bug present.
    (wt / "staged.py").write_text("staged\n")
    assert git("add", "staged.py", cwd=wt).returncode == 0
    (wt / "staged.py").write_text("staged, then edited\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    (wt / "landed.py").write_text("reviewed and committed\n")
    assert git("add", "landed.py", cwd=wt).returncode == 0
    # Commit ONLY that path: a plain `git commit` would also commit the pin's staged
    # file, making the pin's staged blob identical to HEAD and the assertion below
    # vacuous (`git diff --cached` empty for the right reason, not the fixed one).
    assert git("commit", "-qm", "landed", "--", "landed.py", cwd=wt).returncode == 0
    advanced = git("rev-parse", branch, cwd=hub).stdout.strip()

    assert destroy(hub, wt).returncode == 0
    out = run_tool("recover", ref, "--into", str(wt), "--branch", branch, "--force", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr

    cached = git("diff", "--cached", "--name-only", cwd=wt).stdout
    assert "landed.py" not in cached, (
        f"a post-pin commit must not be staged for deletion: {cached!r}"
    )
    assert "big.py" not in cached, f"the pin's own file must not be staged for deletion: {cached!r}"
    assert "staged.py" in cached, f"the pin's own staged half must still be staged: {cached!r}"
    assert git("rev-parse", branch, cwd=hub).stdout.strip() == advanced
    # The pin's content is present as an UNSTAGED diff, which is the promised shape.
    assert (wt / "big.py").read_text() == BIG
    assert "big.py" in git("status", "--porcelain", cwd=wt).stdout


def test_recovering_one_pin_does_not_prune_a_peer_lane(hub: Path) -> None:
    """The stale-registration retry must be SCOPED. `git worktree prune` is repo-wide.

    Measured before the fix: two worktrees both `rm -rf`'d (`ad` peers, the shape this
    tool exists for), recovering one pruned the OTHER's registration, deleted its admin
    index — whose `AD` blob existed nowhere else — and left that blob unreachable, i.e.
    gc food.
    """
    mine = dirty_lane(hub, "wt-mine")
    peer = hub.parent / "wt-peer"
    assert git("worktree", "add", "-q", str(peer), "-b", "lane-peer", cwd=hub).returncode == 0
    # The peer's only copy of a file lives in its INDEX (an `AD` shape).
    (peer / "peer-only.py").write_text("the peer's only copy\n")
    assert git("add", "peer-only.py", cwd=peer).returncode == 0
    (peer / "peer-only.py").unlink()

    pinned = run_tool("pin", cwd=mine)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    shutil.rmtree(mine)
    shutil.rmtree(peer)
    assert (hub / ".git" / "worktrees" / "wt-peer").exists(), "the peer record must exist first"

    out = run_tool("recover", ref, "--into", str(mine), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (hub / ".git" / "worktrees" / "wt-peer").exists(), (
        "recovering one pin must not prune a PEER lane's registration"
    )
    assert (hub / ".git" / "worktrees" / "wt-peer" / "index").exists(), (
        "the peer's admin index holds its only copy of the staged blob"
    )


def test_a_damaged_staged_half_is_reported_not_silently_dropped(hub: Path) -> None:
    """A pin whose staged half is unreadable must not report a clean `recovered N file(s)`.

    `_pin_index_tree` returned "" for BOTH "no staged half" and "staged half damaged", so
    a damaged pin restored the worktree, lost the split, and exited 0 — for an `AD` pin
    the index tree holds the ONLY copy of that content, so that false success IS the loss
    this tool exists to prevent.
    """
    wt = dirty_lane(hub, "wt-dmg")
    (wt / "only-in-index.py").write_text("staged only\n")
    assert git("add", "only-in-index.py", cwd=wt).returncode == 0
    (wt / "only-in-index.py").unlink()
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    index_tree = git("rev-parse", f"{ref}^2^{{tree}}", cwd=hub).stdout.strip()
    assert index_tree, "the pin must record a second parent"
    objects = git(
        "rev-parse", "--path-format=absolute", "--git-path", "objects", cwd=hub
    ).stdout.strip()
    loose = Path(objects) / index_tree[:2] / index_tree[2:]
    assert loose.exists(), f"expected a loose object at {loose}"
    loose.unlink()

    assert destroy(hub, wt).returncode == 0
    out = run_tool("recover", ref, "--into", str(wt), cwd=hub)
    assert out.returncode != 0, f"a damaged staged half must not exit 0: {out.stdout}"
    assert "staged half" in out.stderr, out.stderr
    assert "split it was pinned with" not in out.stdout, "the split must not be claimed"
    assert (wt / "big.py").read_text() == BIG, "the worktree half must still come back"


def test_list_finds_a_pin_from_a_subdirectory(hub: Path) -> None:
    """`pin` keys on the worktree root; `list` must normalise the same way.

    Measured: after `pin --path <wt>/pkg` (accepted, keyed on the worktree),
    `list --path <wt>/pkg` answered "lane pkg-… has no wip pin" — the false "the work is
    gone" verdict this tool must never give.
    """
    wt = dirty_lane(hub, "wt-sub")
    (wt / "pkg").mkdir()
    (wt / "pkg" / "m.py").write_text("x\n")
    pinned = run_tool("pin", "--path", str(wt / "pkg"), cwd=hub)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    listed = run_tool("list", "--path", str(wt / "pkg"), cwd=hub)
    assert listed.returncode == 0, listed.stderr
    assert ref in listed.stdout, listed.stdout
    assert "no wip pin" not in listed.stdout, listed.stdout


def test_a_detached_pin_prints_a_remedy_without_branch(hub: Path) -> None:
    """`--branch HEAD` is refused as soon as the caller's HEAD moves — do not print it."""
    wt = hub.parent / "wt-det"
    assert git("worktree", "add", "-q", "--detach", str(wt), "HEAD", cwd=hub).returncode == 0
    (wt / "x.py").write_text("detached work\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    line = next(ln for ln in pinned.stdout.splitlines() if ln.startswith("recover with"))
    assert "--branch" not in line, line


def test_the_dirty_guard_ignores_repo_config(hub: Path) -> None:
    """`status.showUntrackedFiles=no` must not hide a target's own untracked files.

    Measured: the guard read `[]`, the restore overwrote the target's untracked `big.py`
    without `--force`, and the tool's central safety property was defeated by config.
    """
    wt = dirty_lane(hub, "wt-cfg")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    target = hub.parent / "wt-target"
    assert git("worktree", "add", "-q", str(target), "-b", "lane-target", cwd=hub).returncode == 0
    (target / "big.py").write_text("the target's own untracked file\n")
    assert git("config", "status.showUntrackedFiles", "no", cwd=target).returncode == 0

    out = run_tool("recover", ref, "--into", str(target), cwd=hub)
    assert out.returncode == 2, out.stdout + out.stderr
    assert (target / "big.py").read_text() == "the target's own untracked file\n", (
        "the target's own file must survive"
    )


def test_a_sparse_target_under_restoring_is_a_failure_not_a_success(hub: Path) -> None:
    """`read-tree --reset -u` honours the TARGET's sparse cone, so files can be skipped.

    The count used to be read from the PIN, never from the target, so a sparse target
    printed `recovered N file(s)` and `git status` was clean for the file that never
    appeared — the declared "no success exit over an incomplete restore" class.
    """
    wt = hub.parent / "wt-sparse-src"
    assert git("worktree", "add", "-q", str(wt), "-b", "lane-sparse-src", cwd=hub).returncode == 0
    (wt / "sub").mkdir()
    (wt / "sub" / "deep.py").write_text("deep work\\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    target = hub.parent / "wt-sparse"
    assert git("worktree", "add", "-q", str(target), "-b", "lane-sparse", cwd=hub).returncode == 0
    assert git("sparse-checkout", "set", "--cone", ".", cwd=target).returncode == 0, (
        "sparse-checkout must be available"
    )

    out = run_tool("recover", ref, "--into", str(target), cwd=hub)
    if (target / "sub" / "deep.py").exists():
        # A git that ignores the cone here restores everything: nothing to report.
        assert out.returncode == 0, out.stdout + out.stderr
    else:
        assert out.returncode != 0, f"a skipped file must not be a success: {out.stdout}"
        assert "did NOT appear" in out.stderr, out.stderr


def test_two_worktrees_are_never_put_on_one_live_branch(hub: Path) -> None:
    """`-f` overrides "branch already checked out elsewhere" as well as the stale record.

    Measured: a stale registration plus a LIVE worktree on the same branch produced two
    worktrees on one branch without `--force` — the state where a commit in one rewrites
    the other's base.
    """
    live = hub.parent / "wt-live"
    assert git("worktree", "add", "-q", str(live), "-b", "shared-br", cwd=hub).returncode == 0
    (live / "live.py").write_text("live work\\n")

    mine = dirty_lane(hub, "wt-owner")
    pinned = run_tool("pin", cwd=mine)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    gone = hub.parent / "wt-gone"
    assert git("worktree", "add", "-q", str(gone), "-b", "stale-br", cwd=hub).returncode == 0
    shutil.rmtree(gone)  # stale registration, branch still exists

    out = run_tool("recover", ref, "--into", str(gone), "--branch", "shared-br", cwd=hub)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "checked out in the live worktree" in out.stderr, out.stderr
    holders = [
        ln
        for ln in git("worktree", "list", "--porcelain", cwd=hub).stdout.splitlines()
        if ln.strip() == "branch refs/heads/shared-br"
    ]
    assert len(holders) == 1, holders


def test_a_nested_repository_is_announced_not_silently_lost(hub: Path) -> None:
    """A nested repo is a gitlink: the pin holds a sha whose object lives in ITS `.git`."""
    wt = hub.parent / "wt-nest"
    assert git("worktree", "add", "-q", str(wt), "-b", "lane-nest", cwd=hub).returncode == 0
    nested = wt / "vendored"
    nested.mkdir()
    assert git("init", "-q", ".", cwd=nested).returncode == 0
    git("config", "user.email", "n@example.com", cwd=nested)
    git("config", "user.name", "n", cwd=nested)
    (nested / "inner.txt").write_text("nested committed content\\n")
    assert git("add", "-A", cwd=nested).returncode == 0
    assert git("commit", "-qm", "inner", cwd=nested).returncode == 0

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    assert "nested repository" in pinned.stderr, (
        f"a gitlink must be announced, not silently accepted: {pinned.stderr!r}"
    )
    assert "vendored" in pinned.stderr, pinned.stderr


def test_the_scratch_index_write_tree_failure_is_a_refusal(hub: Path, git_shim: Path) -> None:
    """A `write-tree` that cannot render the SCRATCH index must refuse, not pin.

    That call has its own stderr guard; without a test it could be deleted with the suite
    green (measured: all 45 tests still passed), which is the difference between a guard
    and a comment.
    """
    wt = dirty_lane(hub, "wt-scratch")
    out = _run_tool_with_shim(git_shim, "LW_FAIL_SCRATCH_WRITE", "pin", cwd=wt)
    assert out.returncode != 0, f"a failed scratch write-tree must not pin: {out.stdout}"
    assert "pinned" not in out.stdout, out.stdout
    assert "did not produce a trustworthy tree" in out.stderr, out.stderr


def test_a_non_ascii_filename_is_restored_without_a_false_failure(hub: Path) -> None:
    """`ls-tree --name-only` QUOTES non-ASCII paths; testing that string on disk fails.

    The pin's file list ran through `core.quotePath`, so `café.py` became
    `"caf\303\251.py"` and the post-restore verification looked for a file with quotes
    and octal escapes in its name — reporting a byte-perfect restore as `4 of 5 … did NOT
    appear`, exit 3, with the advice to widen a sparse checkout that was never sparse.
    """
    wt = dirty_lane(hub, "wt-uni")
    (wt / "café.py").write_text("hola\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = run_tool("recover", ref, "--into", str(wt), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (wt / "café.py").read_text() == "hola\n"
    assert "did NOT appear" not in out.stderr


def test_a_dangling_symlink_is_not_reported_missing(hub: Path) -> None:
    """`Path.exists()` follows symlinks, so a restored dangling link read as missing."""
    wt = dirty_lane(hub, "wt-link")
    (wt / "link").symlink_to("/nonexistent/target")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = run_tool("recover", ref, "--into", str(wt), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (wt / "link").is_symlink()


def test_the_staged_delta_keeps_a_non_ascii_name_verbatim(hub: Path) -> None:
    """`diff-tree --raw` quotes paths too, and the quoted string became the FILENAME.

    Measured: the index entry was `"\"caf\\303\\251.py\""` — quotes and escapes as the
    name — and the lane's next bare commit wrote that phantom into history.
    """
    wt = dirty_lane(hub, "wt-uni2")
    branch = "lane-wt-uni2"
    (wt / "café.py").write_text("staged\n")
    assert git("add", "café.py", cwd=wt).returncode == 0
    (wt / "café.py").write_text("staged, then edited\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    (wt / "landed.py").write_text("landed\n")
    assert git("add", "landed.py", cwd=wt).returncode == 0
    assert git("commit", "-qm", "landed", "--", "landed.py", cwd=wt).returncode == 0
    assert destroy(hub, wt).returncode == 0

    out = run_tool("recover", ref, "--into", str(wt), "--branch", branch, "--force", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    cached = git("diff", "--cached", "--name-only", "-z", cwd=wt).stdout.split("\0")
    assert "café.py" in [c for c in cached if c], (
        f"the staged file must be staged under its REAL name: {cached!r}"
    )
    assert not any('"' in c for c in cached if c), f"a quoted phantom name: {cached!r}"


def test_a_failed_scratch_add_is_a_refusal_not_nothing_to_pin(hub: Path, git_shim: Path) -> None:
    """Without the guard, `pin` answers the worst possible verdict: "nothing to pin"."""
    wt = dirty_lane(hub, "wt-addfail")
    out = _run_tool_with_shim(git_shim, "LW_FAIL_ADD", "pin", cwd=wt)
    assert out.returncode != 0, f"a failed `add -A` must not report success: {out.stdout}"
    assert "nothing to pin" not in out.stdout, out.stdout


def test_a_failed_update_index_is_not_claimed_as_a_restored_split(
    hub: Path, git_shim: Path
) -> None:
    """Without the guard, `recover` exits 0 claiming the split with an EMPTY index."""
    wt = dirty_lane(hub, "wt-idxfail")
    branch = "lane-wt-idxfail"
    (wt / "staged.py").write_text("staged\n")
    assert git("add", "staged.py", cwd=wt).returncode == 0
    (wt / "staged.py").write_text("edited\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    (wt / "landed.py").write_text("landed\n")
    assert git("add", "landed.py", cwd=wt).returncode == 0
    assert git("commit", "-qm", "landed", "--", "landed.py", cwd=wt).returncode == 0
    assert destroy(hub, wt).returncode == 0

    out = _run_tool_with_shim(
        git_shim,
        "LW_FAIL_UPDATE_INDEX",
        "recover",
        ref,
        "--into",
        str(wt),
        "--branch",
        branch,
        "--force",
        cwd=hub,
    )
    assert out.returncode != 0, out.stdout
    assert "split it was pinned with" not in out.stdout, out.stdout


def test_a_failed_ref_read_never_says_the_registry_is_empty(hub: Path, git_shim: Path) -> None:
    """Without the guard, a corrupt ref DB reads as "no lane has any wip pin"."""
    wt = dirty_lane(hub, "wt-reffail")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr

    out = _run_tool_with_shim(git_shim, "LW_FAIL_FOR_EACH_REF", "list", "--all", cwd=hub)
    assert out.returncode != 0, out.stdout
    assert "no lane has any wip pin" not in out.stdout, out.stdout


def test_an_old_style_branch_HEAD_pin_still_recovers(hub: Path) -> None:
    """`--branch HEAD` is what pins printed for a detached lane; it must mean "no branch"."""
    wt = hub.parent / "wt-oldstyle"
    assert git("worktree", "add", "-q", "--detach", str(wt), "HEAD", cwd=hub).returncode == 0
    (wt / "det.py").write_text("detached work\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    # The CALLER's HEAD moves on (the stale guard would compare it to the pin's parent).
    (hub / "later.txt").write_text("later\n")
    assert git("add", "later.txt", cwd=hub).returncode == 0
    assert git("commit", "-qm", "later", cwd=hub).returncode == 0

    out = run_tool("recover", ref, "--into", str(wt), "--branch", "HEAD", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (wt / "det.py").read_text() == "detached work\n"


def test_a_subdirectory_is_refused_as_a_recover_target(hub: Path) -> None:
    """`--into <worktree>/pkg` must be refused: `read-tree` is repo-scoped.

    Without the guard the restore rewrote the ENCLOSING worktree root while the message
    named the subdirectory.
    """
    wt = dirty_lane(hub, "wt-root")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    host = hub.parent / "wt-host"
    assert git("worktree", "add", "-q", str(host), "-b", "lane-host", cwd=hub).returncode == 0
    (host / "pkg").mkdir()
    (host / "pkg" / "inner.py").write_text("inner\n")  # git tracks no empty directory
    assert git("add", "-A", cwd=host).returncode == 0
    assert git("commit", "-qm", "pkg", cwd=host).returncode == 0
    before = git("status", "--porcelain", cwd=host).stdout

    out = run_tool("recover", ref, "--into", str(host / "pkg"), cwd=hub)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "is a subdirectory" in out.stderr, out.stderr
    assert git("status", "--porcelain", cwd=host).stdout == before, "the host must be untouched"


def test_a_missing_parent_object_is_named_with_a_salvage_route(hub: Path) -> None:
    """`rev-parse --verify <sha>^1` returns rc 0 for a parent whose OBJECT is gone.

    The old failure surfaced as `worktree add … fatal: invalid reference: <parent>` plus the
    advice "recover <ref> --into <path>" — the command that had just failed, over content
    that is fully readable from the pin's own tree.
    """
    wt = dirty_lane(hub, "wt-noparent")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    parent = git("rev-parse", f"{ref}^1", cwd=hub).stdout.strip()
    objects = git(
        "rev-parse", "--path-format=absolute", "--git-path", "objects", cwd=hub
    ).stdout.strip()
    loose = Path(objects) / parent[:2] / parent[2:]
    if not loose.exists():
        pytest.skip("the parent object is packed in this fixture; nothing to delete")
    loose.unlink()
    assert git("rev-parse", "--verify", "--quiet", f"{ref}^1", cwd=hub).returncode == 0, (
        "the premise: rev-parse still echoes the recorded sha after the object is gone"
    )

    assert destroy(hub, wt).returncode == 0
    out = run_tool("recover", ref, "--into", str(wt), cwd=hub)
    assert out.returncode != 0, out.stdout
    assert "PARENT commit" in out.stderr, out.stderr
    assert "read-tree" in out.stderr, "the message must name a route that WORKS"


def test_an_intent_to_add_entry_is_announced_not_silently_dropped(hub: Path) -> None:
    """`git write-tree` exits 0 while OMITTING every `git add -N` entry.

    So the index view compared equal to HEAD, the pin recorded no staged half, and
    nothing warned — after which `recover` turned the lane's ` A ita.py` into `?? ita.py`
    and its next `git commit -a` silently omitted a file it would have landed.
    """
    wt = clean_lane(hub, "wt-ita")
    (wt / "ita.py").write_text("new work\n")
    assert git("add", "-N", "ita.py", cwd=wt).returncode == 0
    assert git("status", "--porcelain", "ita.py", cwd=wt).stdout.startswith(" A")

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    assert "intent-to-add" in pinned.stderr, (
        f"an intent-to-add entry cannot be restored, so it must be announced: {pinned.stderr!r}"
    )
    assert "ita.py" in pinned.stderr, pinned.stderr


def test_an_unreadable_target_state_is_a_refusal(hub: Path) -> None:
    """If the target's `status` cannot be read, the dirty guard is UNKNOWN — not clean.

    Removing that guard made `recover` overwrite the target's tracked edit and exit 0.
    """
    wt = dirty_lane(hub, "wt-unreadable")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    target = hub.parent / "wt-broken"
    assert git("worktree", "add", "-q", str(target), "-b", "lane-broken", cwd=hub).returncode == 0
    (target / "tracked.txt").write_text("THE TARGET'S OWN LOCAL EDIT\n")
    index = Path(
        git("rev-parse", "--path-format=absolute", "--git-path", "index", cwd=target).stdout.strip()
    )
    data = index.read_bytes()
    index.write_bytes(data[:64])

    out = run_tool("recover", ref, "--into", str(target), cwd=hub)
    assert out.returncode != 0, f"an unreadable target state must not restore: {out.stdout}"
    assert "could not read the state" in out.stderr, out.stderr
    assert "recovered" not in out.stdout


def test_a_failed_diff_tree_is_not_claimed_as_a_restored_split(hub: Path, git_shim: Path) -> None:
    """The third sibling of the staged-restore guards: the DELTA's read must be checked."""
    wt = dirty_lane(hub, "wt-dt")
    branch = "lane-wt-dt"
    (wt / "staged.py").write_text("staged\n")
    assert git("add", "staged.py", cwd=wt).returncode == 0
    (wt / "staged.py").write_text("edited\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    (wt / "landed.py").write_text("landed\n")
    assert git("add", "landed.py", cwd=wt).returncode == 0
    assert git("commit", "-qm", "landed", "--", "landed.py", cwd=wt).returncode == 0
    assert destroy(hub, wt).returncode == 0

    out = _run_tool_with_shim(
        git_shim,
        "LW_FAIL_DIFF_TREE",
        "recover",
        ref,
        "--into",
        str(wt),
        "--branch",
        branch,
        "--force",
        cwd=hub,
    )
    assert out.returncode != 0, out.stdout
    assert "split it was pinned with" not in out.stdout, out.stdout


def test_an_existing_target_does_not_silently_ignore_branch(hub: Path) -> None:
    """`recover --into <existing> --branch <b>` must not exit 0 with HEAD elsewhere."""
    wt = dirty_lane(hub, "wt-br")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    target = hub.parent / "wt-br-target"
    assert (
        git("worktree", "add", "-q", str(target), "-b", "lane-br-target", cwd=hub).returncode == 0
    )
    out = run_tool("recover", ref, "--into", str(target), "--branch", "lane-not-there", cwd=hub)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "--branch" in out.stderr and "lane-not-there" in out.stderr, out.stderr


def test_a_real_ref_failure_is_not_reported_as_a_full_registry(hub: Path, git_shim: Path) -> None:
    """`pin_ref` must tell a lock failure from a taken id."""
    wt = dirty_lane(hub, "wt-ref")
    out = _run_tool_with_shim(git_shim, "LW_FAIL_UPDATE_REF", "pin", cwd=wt)
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "registry is full" not in out.stderr, out.stderr
    assert "could not create" in out.stderr, out.stderr


def test_a_failed_worktree_add_does_not_print_created(hub: Path) -> None:
    """A `worktree add` that fails must not print `created worktree …`."""
    wt = dirty_lane(hub, "wt-badbranch")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = run_tool("recover", ref, "--into", str(wt), "--branch", "bad..name", cwd=hub)
    assert out.returncode != 0, out.stdout
    assert "created worktree" not in out.stdout, out.stdout
    assert "intact" in out.stderr, out.stderr


def test_a_clean_worktree_with_an_unreadable_index_refuses(hub: Path) -> None:
    """The other half of the clean predicate: clean worktree + unreadable index ≠ clean."""
    wt = clean_lane(hub, "wt-cleantrunc")
    index = Path(
        git("rev-parse", "--path-format=absolute", "--git-path", "index", cwd=wt).stdout.strip()
    )
    if not index.exists():
        pytest.skip("the fixture's index lives elsewhere")
    data = index.read_bytes()
    index.write_bytes(data[:64])

    out = run_tool("pin", cwd=wt)
    assert out.returncode != 0, f"'nothing to pin' must not be claimed: {out.stdout}"
    assert "nothing to pin" not in out.stdout, out.stdout


def test_a_damaged_pin_keeps_its_timestamp_and_subject(hub: Path) -> None:
    """`list`'s `cat-file` fallback must still recover the timestamp and the subject.

    A pin whose TREE is deleted is exactly the row an operator has to triage; losing the
    ordering key (empty `when` sorts first) and the subject makes it harder to place.
    """
    wt = dirty_lane(hub, "wt-dmglist")
    pinned = run_tool("pin", "--note", "why this pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    tree = git("rev-parse", f"{ref}^{{tree}}", cwd=hub).stdout.strip()
    objects = git(
        "rev-parse", "--path-format=absolute", "--git-path", "objects", cwd=hub
    ).stdout.strip()
    loose = Path(objects) / tree[:2] / tree[2:]
    if not loose.exists():
        pytest.skip("the tree object is packed in this fixture")
    loose.unlink()

    listed = run_tool("list", "--all", cwd=hub)
    assert listed.returncode == 0, listed.stderr
    row = next(ln for ln in listed.stdout.splitlines() if ref in ln)
    assert "20" in row, f"the timestamp must survive: {row!r}"
    assert "why this pin" in listed.stdout, listed.stdout


def test_list_reads_the_pins_when_the_worktree_is_gone(hub: Path) -> None:
    """`list --path <destroyed>` must not report the registry as unreadable."""
    wt = dirty_lane(hub, "wt-gonelist")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    # Scoped read from a dead path: a state error naming `--all`, not git's `fatal:`.
    scoped = run_tool("list", "--path", str(wt), cwd=hub)
    assert scoped.returncode == 2, f"{scoped.returncode}: {scoped.stdout}{scoped.stderr}"
    assert "does not exist" in scoped.stderr, scoped.stderr
    assert "fatal:" not in scoped.stderr, scoped.stderr

    # …and `--all` still answers, because the refs live in the caller's repo.
    every = run_tool("list", "--all", "--path", str(wt), cwd=hub)
    assert every.returncode == 0, every.stdout + every.stderr
    assert ref in every.stdout, every.stdout


def test_force_does_not_authorise_a_branch_misroute(hub: Path) -> None:
    """`--force` governs the dirty and stale guards ONLY — never the `--branch` check.

    With `and not args.force`, the tool's own advice ("re-run with --force") turned a
    refusal into a SILENT misroute: a CLEAN target on another branch was restored onto
    that branch, exit 0, with `--branch` unmentioned — and the lane's next bare commit
    landed on a branch it never asked for.
    """
    wt = dirty_lane(hub, "wt-fbr")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    target = hub.parent / "wt-fbr-target"
    assert (
        git(
            "worktree", "add", "-q", str(target), "-b", "some-other-branch", "HEAD", cwd=hub
        ).returncode
        == 0
    )
    assert git("status", "--porcelain", cwd=target).stdout.strip() == "", "the target is clean"

    out = run_tool(
        "recover", ref, "--into", str(target), "--branch", "lane-wt-fbr", "--force", cwd=hub
    )
    assert out.returncode == 2, f"a branch request must not be silently ignored: {out.stdout}"
    assert "does not switch an existing worktree" in out.stderr, out.stderr
    assert "does not override this" in out.stderr, "the message must say --force is not a bypass"
    assert (
        git("rev-parse", "--abbrev-ref", "HEAD", cwd=target).stdout.strip() == "some-other-branch"
    )


def test_pin_refuses_an_unborn_head(hub: Path) -> None:
    """No HEAD is a STATE error (exit 2), not a git failure (exit 3)."""
    fresh = hub.parent / "unborn"
    fresh.mkdir()
    assert git("init", "-q", ".", cwd=fresh).returncode == 0
    (fresh / "x.txt").write_text("x\n")

    out = run_tool("pin", "--path", str(fresh), cwd=fresh)
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "no HEAD commit" in out.stderr, out.stderr


def test_into_a_file_names_the_right_remedy(hub: Path) -> None:
    """A FILE target must get "move it aside", not the directory remedy."""
    wt = dirty_lane(hub, "wt-file")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    blocker = hub.parent / "not-a-dir"
    blocker.write_text("in the way\n")
    out = run_tool("recover", ref, "--into", str(blocker), cwd=hub)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "is a file, not a worktree" in out.stderr, out.stderr
    assert "move it aside" in out.stderr, out.stderr


def test_a_failed_real_index_write_tree_is_reported(hub: Path, git_shim: Path) -> None:
    """The REAL-index `write-tree` capture has its own guard, and needs its own test."""
    wt = dirty_lane(hub, "wt-realwrite")
    out = _run_tool_with_shim(git_shim, "LW_FAIL_REAL_WRITE_SILENT", "pin", cwd=wt)
    # It must either refuse or warn that the staged half is absent — never claim it.
    assert "staged state is pinned too" not in out.stdout, out.stdout
    assert out.returncode != 0 or "could not be captured" in out.stderr, (
        f"rc={out.returncode} stdout={out.stdout!r} stderr={out.stderr!r}"
    )
    # ⛔ AND IT MUST SAY WHY. The warning fires whenever the view is absent, so the
    # capture guard's own contribution is the REASON — without it the message degrades to
    # `could not be captured ()`, which tells a lane nothing about what to fix.
    assert "could not be captured ()" not in out.stderr, out.stderr


def test_recovery_into_a_new_path_works_when_the_old_record_is_dead(hub: Path) -> None:
    """`rm -rf` leaves a DEAD holder, whose refusal message differs from the path case.

    git answers `fatal: '<branch>' is already used by worktree at '<dead path>'`, which the
    `already registered worktree` handler never matched — so the tool raised with the
    command that had just failed as its remedy and `--force` did not help, on the exact
    shape this tool exists for.
    """
    wt = dirty_lane(hub, "wt-deadhold")
    branch = "lane-wt-deadhold"
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    shutil.rmtree(wt)  # NOT `worktree remove`: the admin record survives
    assert git("worktree", "list", "--porcelain", cwd=hub).stdout.count("worktree ") >= 2

    elsewhere = hub.parent / "wt-deadhold-new"
    out = run_tool("recover", ref, "--into", str(elsewhere), "--branch", branch, cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (elsewhere / "big.py").read_text() == BIG
    # ⛔ THE OUTCOME, NOT THE MESSAGE. The retry once passed the pin's PARENT as the start
    # point while printing "on <branch>", so git DETACHED: the recovered work never reached
    # the branch, `git branch --contains HEAD` answered `(no branch)`, and the next remover
    # made it gc food — with a success exit and a message saying otherwise.
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=elsewhere).stdout.strip() == branch, (
        "the recovered worktree must be ON the branch"
    )
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=elsewhere).stdout.strip() != "HEAD"


def test_a_parentless_pin_is_refused_with_a_working_route(hub: Path) -> None:
    """Detaching at the SNAPSHOT makes the restore a no-op and the tree reads clean."""
    wt = dirty_lane(hub, "wt-rootpin")
    # A staged half is required for `^2` to exist — the parentless trap is `^2` itself.
    (wt / "staged.py").write_text("staged\n")
    assert git("add", "staged.py", cwd=wt).returncode == 0
    (wt / "staged.py").write_text("edited\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    index_commit = git("rev-parse", f"{ref}^2", cwd=hub).stdout.strip()
    assert index_commit, "the fixture needs a staged half so ^2 exists"

    # `^2` is a ROOT commit by construction — recovering it directly is the trap.
    out = run_tool("recover", index_commit, "--into", str(hub.parent / "wt-rootpin-x"), cwd=hub)
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "no first parent" in out.stderr, out.stderr
    assert "read-tree" in out.stderr, "the message must name a route that works"


def test_a_pin_whose_worktree_tree_is_gone_names_the_surviving_staged_half(hub: Path) -> None:
    """The salvage branch must be reachable AND tested: the staged half may be the only copy."""
    wt = dirty_lane(hub, "wt-dmgtree")
    (wt / "only_in_index.py").write_text("staged work only in the index\n")
    assert git("add", "only_in_index.py", cwd=wt).returncode == 0
    (wt / "only_in_index.py").unlink()
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    tree = git("rev-parse", f"{ref}^{{tree}}", cwd=hub).stdout.strip()
    objects = git(
        "rev-parse", "--path-format=absolute", "--git-path", "objects", cwd=hub
    ).stdout.strip()
    loose = Path(objects) / tree[:2] / tree[2:]
    if not loose.exists():
        pytest.skip("the tree object is packed in this fixture")
    loose.unlink()

    out = run_tool("recover", ref, "--into", str(hub.parent / "wt-dmgtree-x"), cwd=hub)
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "STAGED half survives" in out.stderr, out.stderr
    assert "read-tree" in out.stderr, out.stderr
    # …and the claimed salvage route really works.
    salvage = git("show", f"{ref}^2:only_in_index.py", cwd=hub)
    assert salvage.returncode == 0, salvage.stderr
    assert "staged work only in the index" in salvage.stdout


def test_pin_works_without_a_configured_git_identity(hub: Path) -> None:
    """A fresh CI/cell clone has no `user.email`; the pin writes commits, so it must not fail."""
    wt = dirty_lane(hub, "wt-noid")
    env = {
        "TORTOISE_TEST_CARVE_OUT": "1",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(hub.parent / "empty-home"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "",
        "GIT_AUTHOR_EMAIL": "",
        "GIT_COMMITTER_NAME": "",
        "GIT_COMMITTER_EMAIL": "",
    }
    (hub.parent / "empty-home").mkdir(exist_ok=True)
    run = subprocess.run(
        [PYTHON, str(TOOL), "pin"],
        cwd=str(wt),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "pinned" in run.stdout, run.stdout


def test_assume_unchanged_local_work_is_not_read_as_clean(hub: Path) -> None:
    """`status` cannot see an assume-unchanged modification, but `read-tree` overwrites it.

    Measured: a clean-reading target whose `tracked.txt` was modified and flagged
    `--assume-unchanged` was restored over — exit 0, no `--force`, the target's local work
    gone. This repo records the same defeat elsewhere ("`git diff` is defeated by
    assume-unchanged / skip-worktree"), so the guard must read the INDEX FLAGS.
    """
    wt = dirty_lane(hub, "wt-assume")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    target = hub.parent / "wt-assume-target"
    assert git("worktree", "add", "-q", str(target), "-b", "lane-assume", cwd=hub).returncode == 0
    (target / "tracked.txt").write_text("LOCAL WORK the guard must protect\n")
    assert git("update-index", "--assume-unchanged", "tracked.txt", cwd=target).returncode == 0
    assert git("status", "--porcelain", cwd=target).stdout.strip() == "", (
        "the premise: `status` reports the target clean"
    )

    out = run_tool("recover", ref, "--into", str(target), cwd=hub)
    assert out.returncode != 0, f"hidden local work must not be overwritten: {out.stdout}"
    assert "assume-unchanged" in out.stderr, out.stderr
    assert (target / "tracked.txt").read_text() == "LOCAL WORK the guard must protect\n", (
        "the target's own work must survive"
    )


def test_a_live_holder_at_another_path_is_refused(hub: Path) -> None:
    """The `already used by worktree` handler's LIVE check is load-bearing.

    Without it the run reports `created worktree … on <branch>` and returns 0, putting two
    worktrees on one live branch.
    """
    wt = dirty_lane(hub, "wt-holder")
    branch = "lane-wt-holder"
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    # The branch must be FREE before a live holder can take it: destroy the original
    # worktree first (the branch survives, which is the ordinary #10812 shape).
    assert destroy(hub, wt).returncode == 0
    live = hub.parent / "wt-holder-live"
    assert git("worktree", "add", "-q", str(live), branch, cwd=hub).returncode == 0

    out = run_tool(
        "recover", ref, "--into", str(hub.parent / "wt-holder-new"), "--branch", branch, cwd=hub
    )
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    holders = [
        ln
        for ln in git("worktree", "list", "--porcelain", cwd=hub).stdout.splitlines()
        if ln.strip() == f"branch refs/heads/{branch}"
    ]
    assert len(holders) == 1, f"one branch must have exactly one holder: {holders}"


def test_pin_takes_no_index_lock(hub: Path) -> None:
    """`write-tree` needs `.git/index.lock`; rendering from a COPY means a pin takes none.

    That is what makes "zero-disruption" true: a pin cannot make a concurrent `git add` or
    `commit` in the same lane fail.
    """
    wt = dirty_lane(hub, "wt-lock")
    # A staged half is required for a second parent to exist at all.
    (wt / "staged.py").write_text("staged\n")
    assert git("add", "staged.py", cwd=wt).returncode == 0
    (wt / "staged.py").write_text("edited\n")
    gitdir = Path(git("rev-parse", "--absolute-git-dir", cwd=wt).stdout.strip())
    lock = gitdir / "index.lock"

    try:
        lock.write_text("")  # a concurrent writer holds it
        # A pin must still capture BOTH views: the real-index read renders from a copy.
        out = run_tool("pin", cwd=wt)
        assert out.returncode == 0, out.stdout + out.stderr
        assert "staged state is pinned too" in out.stdout or "index  " in out.stdout, (
            f"the staged half must be captured even while the index is locked: {out.stdout!r}"
        )
        ref = pin_ref(out.stdout)
        assert git("rev-parse", "--verify", "-q", f"{ref}^2", cwd=hub).returncode == 0, (
            "the pin must hold a second parent"
        )
    finally:
        lock.unlink(missing_ok=True)


def test_an_unborn_target_is_not_refused_and_gets_only_the_staged_change(hub: Path) -> None:
    """An orphan worktree is EMPTY — it has nothing to lose, and must not get the base tree.

    Two defects met here: `rev-parse HEAD` echoes its argument on an unborn branch, so the
    stale guard refused the empty target as holding work ("is at HEAD … would LOSE work"),
    and once past it the unborn target took the whole-tree path, staging the pin's ENTIRE
    base tree while claiming "the staged/unstaged split it was pinned with".
    """
    wt = dirty_lane(hub, "wt-orph")
    (wt / "staged.py").write_text("the only staged file\n")
    assert git("add", "staged.py", cwd=wt).returncode == 0
    (wt / "staged.py").write_text("edited after staging\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    orphan = hub.parent / "wt-orphan"
    assert (
        git("worktree", "add", "--orphan", "-b", "lane-orphan", str(orphan), cwd=hub).returncode
        == 0
    )
    assert git("rev-parse", "--verify", "-q", "HEAD", cwd=orphan).returncode != 0, "unborn"

    # No --force: an empty target has nothing a restore could overwrite.
    out = run_tool("recover", ref, "--into", str(orphan), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr

    cached = git("diff", "--cached", "--name-only", cwd=orphan).stdout.split()
    assert cached == ["staged.py"], f"only the pin's STAGED change may be staged: {cached!r}"
    status = git("status", "--porcelain", cwd=orphan).stdout
    assert "?? big.py" in status, status
    assert "?? tracked.txt" in status or "?? tracked.txt" not in status


def test_a_target_inside_a_peer_worktree_is_refused(hub: Path) -> None:
    """A nested worktree becomes a gitlink in the peer's next `git add -A`."""
    wt = dirty_lane(hub, "wt-nest1")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    peer = hub.parent / "wt-peer-nest"
    assert git("worktree", "add", "-q", str(peer), "-b", "lane-peer-nest", cwd=hub).returncode == 0
    out = run_tool(
        "recover", ref, "--into", str(peer / "newsub"), "--branch", "lane-wt-nest1", cwd=hub
    )
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "NESTED worktree" in out.stderr, out.stderr
    assert not (peer / "newsub").exists(), "nothing may be created inside the peer"


def test_a_staged_deletion_survives_the_delta(hub: Path) -> None:
    """The delta's `D` branch: a `git rm` the lane staged must come back STAGED as a delete."""
    wt = dirty_lane(hub, "wt-rm")
    branch = "lane-wt-rm"
    # `dirty_lane` already deleted doomed.txt in the WORKTREE; stage the deletion.
    assert git("rm", "--cached", "doomed.txt", cwd=wt).returncode == 0
    (wt / "doomed.txt").unlink(missing_ok=True)
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    (wt / "landed.py").write_text("landed\n")
    assert git("add", "landed.py", cwd=wt).returncode == 0
    assert git("commit", "-qm", "landed", "--", "landed.py", cwd=wt).returncode == 0
    assert destroy(hub, wt).returncode == 0

    out = run_tool("recover", ref, "--into", str(wt), "--branch", branch, "--force", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    deleted = git("diff", "--cached", "--name-only", "--diff-filter=D", cwd=wt).stdout.split()
    assert "doomed.txt" in deleted, f"the staged deletion must survive: {deleted!r}"
    assert not (wt / "doomed.txt").exists()


def test_a_skip_worktree_target_is_refused(hub: Path) -> None:
    """skip-worktree prints UPPERCASE `S`, so `tag.islower()` missed it."""
    wt = dirty_lane(hub, "wt-skip")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    target = hub.parent / "wt-skip-target"
    assert git("worktree", "add", "-q", str(target), "-b", "lane-skip", cwd=hub).returncode == 0
    (target / "tracked.txt").write_text("LOCAL WORK behind skip-worktree\n")
    assert git("update-index", "--skip-worktree", "tracked.txt", cwd=target).returncode == 0
    assert git("status", "--porcelain", cwd=target).stdout.strip() == ""

    out = run_tool("recover", ref, "--into", str(target), cwd=hub)
    assert out.returncode != 0, f"hidden local work must not be overwritten: {out.stdout}"
    assert "skip-worktree" in out.stderr, out.stderr
    assert (target / "tracked.txt").read_text() == "LOCAL WORK behind skip-worktree\n"


def test_force_makes_the_live_holder_remedy_actually_work(hub: Path) -> None:
    """The refusal prints "--force" as the remedy, so --force must run it."""
    wt = dirty_lane(hub, "wt-holder2")
    branch = "lane-wt-holder2"
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    live = hub.parent / "wt-holder2-live"
    assert git("worktree", "add", "-q", str(live), branch, cwd=hub).returncode == 0

    refused = run_tool(
        "recover", ref, "--into", str(hub.parent / "wt-h2-new"), "--branch", branch, cwd=hub
    )
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "--force" in refused.stderr

    forced = run_tool(
        "recover",
        ref,
        "--into",
        str(hub.parent / "wt-h2-new"),
        "--branch",
        branch,
        "--force",
        cwd=hub,
    )
    assert forced.returncode == 0, forced.stdout + forced.stderr


def test_a_silent_branch_misroute_is_caught_not_printed(hub: Path, git_shim: Path) -> None:
    """The outcome is asserted: a `worktree add` that detaches must not print "on <branch>"."""
    wt = dirty_lane(hub, "wt-misroute")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = _run_tool_with_shim(
        git_shim,
        "LW_DETACH_WORKTREE_ADD",
        "recover",
        ref,
        "--into",
        str(hub.parent / "wt-misroute-new"),
        "--branch",
        "lane-wt-misroute",
        cwd=hub,
    )
    assert out.returncode != 0, f"a detached worktree must not pass for the branch: {out.stdout}"
    assert "on lane-wt-misroute" not in out.stdout, out.stdout


def test_the_fleets_own_lane_directory_is_recoverable(hub: Path) -> None:
    """`<repo>/.worktrees/<lane>` is the layout RECIPE step 2 prescribes, and must work.

    The nested-worktree guard read the enclosing root as "some worktree" and refused the
    fleet's own lane path — its remedy being "restore into <enclosing>", i.e. restore a
    lane's snapshot on top of the MAIN CHECKOUT. The hazard is a path the enclosing
    worktree would RECORD; a path it IGNORES can never become a gitlink.
    """
    ignore = hub / ".gitignore"
    ignore.write_text(ignore.read_text() + ".worktrees/\n")
    assert git("add", ".gitignore", cwd=hub).returncode == 0
    assert git("commit", "-qm", "ignore lane dir", cwd=hub).returncode == 0

    lane_dir = hub / ".worktrees" / "wt-fleet"
    lane_dir.parent.mkdir(exist_ok=True)
    assert git("worktree", "add", "-q", str(lane_dir), "-b", "lane-fleet", cwd=hub).returncode == 0
    (lane_dir / "work.py").write_text("the lane's work\n")
    pinned = run_tool("pin", cwd=lane_dir)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, lane_dir).returncode == 0

    # The exact command RECIPE step 2 prints, with no --force.
    out = run_tool("recover", ref, "--into", str(lane_dir), "--branch", "lane-fleet", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (lane_dir / "work.py").read_text() == "the lane's work\n"


def test_a_path_whose_name_starts_with_a_space_round_trips(hub: Path) -> None:
    """`_out(...).strip()` ate the leading space of the FIRST pinned path."""
    wt = dirty_lane(hub, "wt-space")
    (wt / " lead.txt").write_text("leading space\n")
    (wt / "zz.txt").write_text("sorts last\n")
    assert git("add", " lead.txt", cwd=wt).returncode == 0
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    assert destroy(hub, wt).returncode == 0
    # `" lead.txt"` sorts FIRST, so its leading space sat under `strip()`; the old read
    # reported "1 of N file(s) … ` lead.txt` did NOT appear" over a perfect restore (rc=3).
    out = run_tool("recover", ref, "--into", str(wt), "--branch", "lane-wt-space", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (wt / " lead.txt").read_text() == "leading space\n"
    staged = git("diff", "--cached", "--name-only", "-z", cwd=wt).stdout
    assert " lead.txt" in staged.split("\0"), staged
