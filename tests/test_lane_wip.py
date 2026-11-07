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

import contextlib
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
        # a non-UTF-8 path is a shape under test, so the harness must not choke on it
        errors="surrogateescape",
        check=False,
    )


def run_tool(*args: str, cwd: Path, timeout: float | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [PYTHON, str(TOOL), *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            errors="surrogateescape",
            # ⛔ STRICT stdout ON PURPOSE. `PYTHONIOENCODING` is unset here, so Python picks
            # `surrogateescape` for stdout under this box's C.UTF-8 locale and hides an
            # `UnicodeEncodeError` that a lane on an `en_US.utf8` host hits on its PRIMARY verb
            # (measured: `pin` exiting 1 after writing the ref; `recover` exiting 1 between
            # `worktree add` and `read-tree`, leaving a clean, empty worktree). The tool makes
            # its own output byte-safe, so every test runs under the strictest setting.
            env={**os.environ, "PYTHONIOENCODING": "utf-8:strict"},
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"`lane_wip.py {' '.join(args)}` did not finish within {timeout}s in {cwd} — a "
            "blocked read writes NO ref and prints nothing, so a caller with a timeout "
            "proceeds unpinned and the lane's readable work is destroyed"
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


def test_an_empty_pin_list_names_the_repository_it_read(hub: Path) -> None:
    """A SCOPED empty list must not read as the unscoped fact "the work is gone".

    The pins are read from `root`, which can be a DIFFERENT repo than the caller's
    (`pin --path <other repo>` writes the ref there), and RECIPE step 3 tells the operator
    that an EMPTY list means "the work is genuinely gone". So an unqualified "no pin" is
    that false verdict: measured, `list --all` from repoA answered "no lane has any wip
    pin" at exit 0 while the pin sat one ref away in repoB, and the operator who follows
    the recipe stops looking.
    """
    empty = dirty_lane(hub, "wt-empty")
    assert run_tool("list", "--all", cwd=empty).returncode == 0
    all_out = run_tool("list", "--all", cwd=empty)
    assert "no lane has any wip pin" in all_out.stdout
    # `root` is the worktree the answer was read from (`repo_root()`), which is the
    # repository whose ref namespace was actually scanned.
    assert str(empty.resolve()) in all_out.stdout, (
        f"the empty answer must name the repository it read: {all_out.stdout!r}"
    )

    lane_out = run_tool("list", cwd=empty)
    assert "has no wip pin" in lane_out.stdout
    assert str(empty.resolve()) in lane_out.stdout, (
        f"the lane-scoped empty answer must name its repository too: {lane_out.stdout!r}"
    )

    recipe = run_tool("recipe", cwd=empty)
    assert recipe.returncode == 0, recipe.stderr
    assert "pin list is EMPTY" in recipe.stdout
    assert "in this repository" in recipe.stdout, (
        "RECIPE must qualify the EMPTY-list conclusion to the repository it read, or it "
        f"advises giving up on work pinned elsewhere: {recipe.stdout!r}"
    )


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


def test_the_remedy_for_a_cross_repo_pin_names_the_repository_that_holds_it(hub: Path) -> None:
    """`pin --path <other repo>` lives in THAT repo, so the printed remedy must say so.

    The ref is minted in the PINNED repo (see the test above), but the printed remedy named
    neither that repo nor `--path` — so it assumed the CALLER's repo. Measured from the
    caller: the printed command exited 2 "'<ref>' does not name a commit in this repository",
    and the `list --all` it advises answered "no lane has any wip pin" at exit 0 while the
    pin sat one ref away. That is the false "the work is gone" verdict this module documents
    it must never give, produced by its own primary verb.
    """
    other_hub = hub.parent / "cross-hub"
    other_hub.mkdir()
    assert git("init", "-q", ".", cwd=other_hub).returncode == 0
    git("config", "user.email", "t@example.com", cwd=other_hub)
    git("config", "user.name", "t", cwd=other_hub)
    (other_hub / "f.txt").write_text("base\n")
    git("add", "-A", cwd=other_hub)
    assert git("commit", "-qm", "base", cwd=other_hub).returncode == 0

    lane = hub.parent / "cross-lane"
    made = git("worktree", "add", "-q", str(lane), "-b", "cross-branch", cwd=other_hub)
    assert made.returncode == 0, made.stderr
    (lane / "big.py").write_text("the lane's work\n")

    pinned = run_tool("pin", "--path", str(lane), cwd=hub)  # `hub` is a DIFFERENT repo
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    ref = pin_ref(pinned.stdout)
    remedy = next(ln for ln in pinned.stdout.splitlines() if "recover with:" in ln)
    assert f"--path {lane}" in remedy, (
        f"the remedy must name the repository that holds the pin: {remedy}"
    )

    assert destroy(other_hub, lane).returncode == 0

    def restore(cwd: Path) -> subprocess.CompletedProcess:
        return run_tool(
            "recover",
            ref,
            "--path",
            str(lane),
            "--into",
            str(lane),
            "--branch",
            "cross-branch",
            cwd=cwd,
        )

    # From the CALLER's repo the ref is invisible — and it must SAY so, not answer "no pins".
    wrong = restore(hub)
    assert wrong.returncode != 0, wrong.stdout + wrong.stderr
    assert "does not name a commit" in wrong.stdout + wrong.stderr, wrong.stdout + wrong.stderr

    # From the repository the remedy names, it recovers the work.
    out = restore(other_hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (lane / "big.py").read_text() == "the lane's work\n"


def test_a_note_that_starts_with_the_trailer_words_survives(hub: Path) -> None:
    """The tool's own trailer must be stripped by POSITION, never by prefix.

    A caller's note is free to begin with "pinned at …"; filtering on that prefix
    silently dropped the note and kept the boilerplate instead.

    ⛔ AND THE SAME IS TRUE OF THE NOTE'S **LAST** LINE, which is the only line the
    strip can actually reach. `snapshot` separates the note from its trailer with a
    blank line, so what the strip sees after popping `pinned at <ts>` is that blank —
    never the caller's text. That is a property of the WRITER, not of the strip, so it
    is pinned here for every trailer word the reader has ever stripped (a former
    `index-tree ` entry was removed as unreachable; a future one that appends after the
    trailer instead of before it would break this). The second case is the one an
    earlier version of this file got wrong in its COMMENT — it claimed to strip
    `index-tree` "before" `pinned at` while the loop could never reach a caller's line.
    """
    for i, note in enumerate(("pinned at noon, why not", "first line\nindex-tree deadbeef")):
        # ⛔ A DETERMINISTIC name, like every other `dirty_lane` caller in this file
        # (`wt-forge`, `wt-lane`, `wt-idxonly`). Deriving it from the note would need
        # `hash()`, which is salted per interpreter, so the two iterations could
        # collide — and a collision reds a merge-gating test for no reason at
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


def test_a_staged_rename_does_not_invent_a_dirty_path(hub: Path) -> None:
    """Porcelain `-z` emits a rename's ORIGINAL path as a bare second field.

    That field carries no `XY ` prefix, so a parser applying `entry[3:]` to every chunk
    turns `src/old.py` into a phantom `/old.py` — a path `git status` never called dirty.
    The clean cross-check then demanded the pin hold it and refused a perfectly good pin
    at exit 3 with the false claim that `git status` reported a change the pin does not
    hold. Measured before the fix: `['/old.py']` from a lane whose only change was a clean
    `git mv`, and `pin` exited 3.
    """
    # ⛔ THE PHANTOM MUST COLLIDE WITH A FILE THAT EXISTS, or `_pin_lacks_change` skips it as
    # a DELETION ("recorded by absence") and the bug is invisible. The phantom a mis-parsed
    # rename invents is `orig[3:]`, so renaming `abcnotes.txt` invents `notes.txt` — made to
    # exist here as an IGNORED file, which the pin legitimately does not hold.
    (hub / "abcnotes.txt").write_text("notes at base\n")
    assert git("add", "abcnotes.txt", cwd=hub).returncode == 0
    assert git("commit", "-qm", "notes", cwd=hub).returncode == 0

    wt = clean_lane(hub, "wt-rename")
    (wt / "src").mkdir()
    # ⛔ THE SOURCE MUST BE UNCHANGED relative to HEAD, or git reports `D`+`A` instead of a
    # rename and the bare-original-path field under test never appears. A CLEAN rename is
    # 100% similar, so `-z` emits `R  src/renamed.txt\0abcnotes.txt`.
    assert git("mv", "abcnotes.txt", "src/renamed.txt", cwd=wt).returncode == 0
    raw = git("status", "--porcelain", "-z", "--untracked-files=normal", cwd=wt).stdout
    assert raw.startswith("R  "), f"fixture must produce a clean staged rename: {raw!r}"
    (wt / ".gitignore").write_text("ignored/\nnotes.txt\n")
    (wt / "notes.txt").write_text("ignored scratch, never pinned\n")

    pinned = run_tool("pin", cwd=wt)
    combined = pinned.stdout + pinned.stderr
    assert pinned.returncode == 0, combined
    assert "does NOT hold" not in combined, (
        f"a clean staged rename must not fabricate a path the pin cannot hold: {combined!r}"
    )
    assert pin_ref(pinned.stdout), combined
    # The phantom only ever reached the CLEAN cross-check, so a pin that succeeded and
    # names its ref is the proof it was never demanded. Recover the rename end-to-end so a
    # swallowed rename (captured as `D` with the new path missing) also fails here.
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0
    out = run_tool("recover", ref, "--into", str(wt), "--branch", "lane-wt-rename", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (wt / "src" / "renamed.txt").is_file(), out.stdout
    assert not (wt / "abcnotes.txt").exists(), "the rename's old path must be gone"


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
        fi
        # rc 0 WITH `error:` NOISE: a garbled index. The guard must key on the noise, not
        # only on the return code — the `_index_tree` twin has had a test for this for
        # rounds; the scratch twin did not.
        if [ -n "$LW_SCRATCH_WRITE_NOISE" ]; then
          echo "error: cache entry has null sha1: deadbeef" >&2
          echo "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
          exit 0
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
  status)
    [ -n "$LW_FAIL_STATUS" ] && {{ echo "shim: refusing status" >&2; exit 128; }} ;;
  hash-object)
    [ -n "$LW_FAIL_HASH_OBJECT" ] && {{ echo "shim: refusing hash-object" >&2; exit 128; }} ;;
  add)
    [ -n "$LW_FAIL_ADD" ] && {{ echo "shim: refusing add" >&2; exit 128; }}
    if [ -n "$LW_FAIL_FORCE_ADD" ]; then
      for a in "$@"; do
        if [ "$a" = "-f" ]; then
          echo "fatal: unable to write new index file" >&2
          exit 128
        fi
      done
    fi
    if [ -n "$LW_OLD_GIT_ADD" ]; then
      for a in "$@"; do
        if [ "$a" = "--sparse" ]; then
          echo "shim: error: unknown option sparse" >&2
          exit 129
        fi
      done
    fi ;;
  update-index)
    [ -n "$LW_FAIL_UPDATE_INDEX" ] && {{ echo "shim: refusing update-index" >&2; exit 128; }} ;;
  rev-parse)
    if [ -n "$LW_FAIL_INSIDE_TREE" ]; then
      for a in "$@"; do
        [ "$a" = "--is-inside-work-tree" ] && {{
          echo "shim: refusing --is-inside-work-tree" >&2; exit 128
        }}
      done
    fi
    if [ -n "$LW_FAIL_SYMBOLIC_REF" ]; then
      for a in "$@"; do
        [ "$a" = "symbolic-ref" ] && {{ echo "shim: refusing symbolic-ref" >&2; exit 128; }}
      done
    fi
    if [ -n "$LW_FAIL_REV_HEAD_ONCE" ]; then
      for a in "$@"; do
        if [ "$a" = "HEAD" ]; then
          if [ -e "$LW_FAIL_REV_HEAD_ONCE" ]; then :; else
            : > "$LW_FAIL_REV_HEAD_ONCE"
            echo "shim: refusing the FIRST rev-parse HEAD" >&2; exit 128
          fi
        fi
      done
    fi
    if [ -n "$LW_FAIL_VERIFY_QUIET_REF" ]; then
      for a in "$@"; do
        case "$a" in
          refs/pi-lane-wip/*) echo "shim: refusing rev-parse $a" >&2; exit 128 ;;
        esac
      done
    fi
    if [ -n "$LW_FAIL_EXACT_HEAD" ]; then
      for a in "$@"; do
        [ "$a" = "HEAD" ] && {{ echo "shim: refusing rev-parse HEAD" >&2; exit 128; }}
      done
    fi
    if [ -n "$LW_FAIL_REV_HEAD" ]; then
      for a in "$@"; do
        [ "$a" = "HEAD" ] && {{ echo "shim: refusing rev-parse HEAD" >&2; exit 128; }}
      done
    fi
    if [ -n "$LW_FAIL_STAGED_TREE" ]; then
      for a in "$@"; do
        case "$a" in
          *'^{{tree}}') echo "shim: refusing rev-parse $a" >&2; exit 128 ;;
        esac
      done
    fi
    if [ -n "$LW_FAIL_REV_TIP" ]; then
      for a in "$@"; do
        case "$a" in
          refs/heads/*) echo "shim: refusing rev-parse $a" >&2; exit 128 ;;
        esac
      done
    fi
    if [ -n "$LW_FAIL_REV_SECOND" ]; then
      for a in "$@"; do
        case "$a" in
          *'^2') echo "shim: refusing rev-parse $a" >&2; exit 128 ;;
        esac
      done
    fi ;;
  check-ignore)
    [ -n "$LW_FAIL_CHECKIGNORE" ] && {{ echo "shim: refusing check-ignore" >&2; exit 128; }} ;;
  cat-file)
    [ -n "$LW_FAIL_CAT_FILE" ] && {{ echo "shim: refusing cat-file" >&2; exit 128; }} ;;
  rev-parse-verify-q)
    : ;;
  copy-armed-noop)
    : ;;
  symbolic-ref)
    [ -n "$LW_FAIL_SYMBOLIC_REF" ] && {{ echo "shim: refusing symbolic-ref" >&2; exit 128; }} ;;
  ls-files)
    if [ -n "$LW_FAIL_LSFILES_PLAIN" ]; then
      for a in "$@"; do
        if [ "$a" = "-z" ]; then
          # ONLY the bare `ls-files -z` (the index-only enumeration); --stage/-v are other arms
          case " $* " in
            *" --stage "*|*" -v "*) : ;;
            *) echo "shim: refusing ls-files -z" >&2; exit 128 ;;
          esac
        fi
      done
    fi
    if [ -n "$LW_FAIL_LS_FILES_STAGE" ]; then
      for a in "$@"; do
        [ "$a" = "--stage" ] && {{ echo "shim: refusing ls-files --stage" >&2; exit 128; }}
      done
    fi
    if [ -n "$LW_FAIL_LS_FILES" ] && [ "$2" = "-v" ]; then
      echo "shim: refusing ls-files -v" >&2; exit 128
    fi ;;
  ls-tree)
    if [ -n "$LW_FAIL_LS_TREE" ]; then
      # ONLY the gitlink probe: `-r -z <tree>` with no --name-only. Failing every ls-tree
      # would be caught by an unrelated check=True read, so the test would pass whether or
      # not the probe's own guard exists.
      has_name_only=
      for a in "$@"; do [ "$a" = "--name-only" ] && has_name_only=1; done
      if [ -z "$has_name_only" ]; then
        echo "shim: refusing ls-tree (gitlink probe)" >&2; exit 128
      fi
    fi ;;
  for-each-ref)
    [ -n "$LW_FAIL_FOR_EACH_REF" ] && {{ echo "shim: refusing for-each-ref" >&2; exit 128; }} ;;
  diff-tree)
    [ -n "$LW_FAIL_DIFF_TREE" ] && {{ echo "shim: refusing diff-tree" >&2; exit 128; }} ;;
  show-ref)
    if [ -n "$LW_FAIL_SHOW_REF" ]; then
      echo "shim: refusing show-ref" >&2; exit 128
    fi ;;
  update-ref)
    [ -n "$LW_FAIL_UPDATE_REF" ] && {{ echo "shim: cannot lock ref" >&2; exit 128; }} ;;
  worktree)
    if [ -n "$LW_FAIL_WORKTREE_LIST" ] && [ "$2" = "list" ]; then
      echo "shim: refusing worktree list" >&2; exit 128
    fi
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


def _run_tool_with_shim(
    shim: Path,
    env_key: str,
    *args: str,
    cwd: Path,
    value: str = "1",
    also: tuple[str, ...] = (),
):
    env = dict(os.environ)
    env["PATH"] = f"{shim}{os.pathsep}{env.get('PATH', '')}"
    env[env_key] = value
    for extra in also:
        env[extra] = "1"
    return subprocess.run(
        [PYTHON, str(TOOL), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        errors="surrogateescape",
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
    assert "populated subdirectory" in out.stderr, out.stderr
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
    # ⛔ "PATH MISSING" IS AN UNKNOWN, NOT "DEAD", AND IT MUST SAY SO. A worktree moved with
    # plain `mv` keeps its OLD path registered, so the same record is either a dead holder
    # or a LIVE one; git cannot tell them apart (both read `prunable`). Refusing without
    # `--force` is what stops two live worktrees on one branch — where one lane's commit
    # rewrites the other's base (measured).
    refused = run_tool("recover", ref, "--into", str(elsewhere), "--branch", branch, cwd=hub)
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "EITHER a dead record" in refused.stderr, refused.stderr
    assert "--force" in refused.stderr, refused.stderr
    assert not elsewhere.exists(), "the refusal must not have created anything"

    out = run_tool("recover", ref, "--into", str(elsewhere), "--branch", branch, "--force", cwd=hub)
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
    assert "?? tracked.txt" in status, status


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
    # ⛔ THE REFUSAL MUST NAME A REMEDY THAT WORKS. The old message said "would OVERWRITE
    # them" (false over a clean cone target, where `read-tree` SKIPS `S` paths) and told the
    # caller to run `--no-assume-unchanged`, which does NOT clear `S` (measured: the flag
    # stayed `S`; `--no-skip-worktree` did). Name the sparse route instead.
    assert "skip-worktree" in out.stderr, out.stderr
    assert "sparse-checkout disable" in out.stderr, out.stderr
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


def test_an_absent_index_is_not_invented_and_does_not_empty_the_branch(hub: Path) -> None:
    """No index must mean "not captured" — never the EMPTY TREE, which reads "all deleted".

    The old fallback rendered the REAL index, so an absent `.git/index` pinned
    `4b825dc6…` as the staged half and announced "staged state is pinned too"; `recover`
    then staged the WHOLE tree as deleted, and a bare `git commit` on the recovered branch
    deleted everything.
    """
    wt = dirty_lane(hub, "wt-noindex")
    (wt / ".git").unlink() if (wt / ".git").is_symlink() else None
    index = Path(
        git("rev-parse", "--path-format=absolute", "--git-path", "index", cwd=wt).stdout.strip()
    )
    index.unlink()
    assert not index.exists()

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    assert "could not be captured" in pinned.stderr, pinned.stderr
    assert "staged state is pinned too" not in pinned.stdout, pinned.stdout
    # A pin may exist, but it must carry NO second parent claiming a staged half.
    ref = pin_ref(pinned.stdout)
    assert git("rev-parse", "--verify", "-q", f"{ref}^2", cwd=hub).returncode != 0, (
        "an uncaptured index must not become a second parent"
    )
    assert git("rev-parse", f"{ref}^2^{{tree}}", cwd=hub).returncode != 0


def test_one_unreadable_file_does_not_cost_the_lane_its_whole_pin(hub: Path) -> None:
    """`git add -A` is all-or-nothing; one mode-000 file must not yield ZERO pins."""
    wt = dirty_lane(hub, "wt-unreadable")
    blocked = wt / "rootish.bin"
    blocked.write_text("other user\n")
    blocked.chmod(0o000)
    try:
        pinned = run_tool("pin", cwd=wt)
        assert pinned.returncode == 0, pinned.stdout + pinned.stderr
        assert "could NOT be added" in pinned.stderr, pinned.stderr
        assert "rootish.bin" in pinned.stderr, pinned.stderr
        ref = pin_ref(pinned.stdout)
        # The lane's real work IS in the pin, and comes back.
        assert destroy(hub, wt).returncode == 0
        out = run_tool("recover", ref, "--into", str(wt), "--branch", "lane-wt-unreadable", cwd=hub)
        assert out.returncode == 0, out.stdout + out.stderr
        assert (wt / "big.py").exists(), "the lane's 4,500-line file must be recovered"
        assert (wt / "tracked.txt").read_text() != "v1\n"
    finally:
        # the worktree was destroyed, or the path was never indexed
        with contextlib.suppress(FileNotFoundError):
            blocked.chmod(0o600)


def test_a_target_two_levels_inside_a_peer_worktree_is_refused(hub: Path) -> None:
    """The walk-up to the nearest EXISTING ancestor is what makes the guard fire here."""
    wt = dirty_lane(hub, "wt-deep1")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    peer = hub.parent / "wt-peer-deep"
    assert git("worktree", "add", "-q", str(peer), "-b", "lane-peer-deep", cwd=hub).returncode == 0
    deep = peer / "a" / "b"
    assert not deep.parent.exists(), "the walk-up only matters when the parent is ABSENT"

    out = run_tool("recover", ref, "--into", str(deep), "--branch", "lane-wt-deep1", cwd=hub)
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "NESTED worktree" in out.stderr, out.stderr
    assert not deep.exists()
    assert not (peer / "a").exists()


def test_a_non_utf8_filename_round_trips_byte_exactly(hub: Path) -> None:
    """A latin-1 path must not become U+FFFD at the Python<->git boundary.

    `_git(errors="replace")` mangled any non-UTF-8 path, so the pin's `AD` shape came back
    as a PHANTOM index entry (`caf\\ufffd.txt`) with exit 0 — a bare commit lands it — and
    the worktree half was reported missing over a byte-perfect restore. Checked on the
    `-z` BYTES, not on `status` text.
    """
    wt = dirty_lane(hub, "wt-latin")
    raw = b"caf\xe9.txt"
    name = os.fsdecode(raw)  # surrogateescape: the exact bytes the filesystem holds
    target = wt / name
    with open(target, "wb") as fh:
        fh.write(b"latin-1 name\n")
    assert git("add", "-A", cwd=wt).returncode == 0
    target.unlink()  # `AD`: the blob now exists ONLY in the index — nothing else holds it

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    ref = pin_ref(pinned.stdout)

    orphan = hub.parent / "wt-latin-orphan"
    assert (
        git("worktree", "add", "--orphan", "-b", "lane-latin", str(orphan), cwd=hub).returncode == 0
    )
    out = run_tool("recover", ref, "--into", str(orphan), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr

    index_bytes = subprocess.run(  # raw bytes: the helper decodes UTF-8 strictly
        ["git", "-C", str(orphan), "ls-files", "-z"], capture_output=True, check=False
    ).stdout
    assert raw in index_bytes.split(b"\0"), f"byte-exact name must reach the index: {index_bytes!r}"
    assert b"\xef\xbf\xbd" not in index_bytes, "no U+FFFD may be minted"


def test_a_staged_deletion_of_a_dashed_name_survives_the_delta(hub: Path) -> None:
    """A bare path after `update-index --force-remove` reads as a switch without `--`."""
    wt = dirty_lane(hub, "wt-dash")
    (wt / "-dash.txt").write_text("about to be deleted\n")
    assert git("add", "--", "-dash.txt", cwd=wt).returncode == 0
    assert git("commit", "-qm", "add dash", "--", "-dash.txt", cwd=wt).returncode == 0

    (wt / "-dash.txt").unlink()
    assert git("rm", "--cached", "--", "-dash.txt", cwd=wt).returncode == 0
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    ref = pin_ref(pinned.stdout)

    # Advance the branch so `recover` takes the DELTA path.
    (wt / "after.py").write_text("after\n")
    assert git("add", "after.py", cwd=wt).returncode == 0
    assert git("commit", "-qm", "after", "--", "after.py", cwd=wt).returncode == 0

    out = run_tool(
        "recover", ref, "--into", str(wt), "--branch", "lane-wt-dash", "--force", cwd=hub
    )
    assert out.returncode == 0, out.stdout + out.stderr
    deleted = git("diff", "--cached", "--name-only", "--diff-filter=D", "-z", cwd=wt).stdout
    assert "-dash.txt" in deleted.split("\0"), deleted


def test_a_worktree_whose_name_ends_in_a_space_can_be_pinned(hub: Path) -> None:
    """`strip()` ate the trailing space of the DIRECTORY NAME, so no pin was possible.

    The verdict it produced was "that worktree is gone, so there is nothing left to pin" —
    for a live lane, and on the tool whose whole job is not to say that.
    """
    lane = hub.parent / "wt-space-name "
    assert git("worktree", "add", "-q", str(lane), "-b", "lane-space-name", cwd=hub).returncode == 0
    (lane / "big.py").write_text("the lane's work\n")

    pinned = run_tool("pin", cwd=lane)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    ref = pin_ref(pinned.stdout)

    listed = run_tool("list", cwd=lane)
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert "gone" not in listed.stdout.lower(), listed.stdout

    assert destroy(hub, lane).returncode == 0
    out = run_tool("recover", ref, "--into", str(lane), "--branch", "lane-space-name", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (lane / "big.py").read_text() == "the lane's work\n"


def test_recover_from_a_dead_path_does_not_blame_the_ref(hub: Path) -> None:
    """A destroyed `--path` is the state this tool exists for; the PIN must still be found."""
    wt = dirty_lane(hub, "wt-deadpath")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0
    assert not wt.exists()

    target = hub.parent / "wt-deadpath-recovery"
    out = run_tool("recover", ref, "--path", str(wt), "--into", str(target), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "does not name a commit" not in out.stderr, out.stderr
    assert (target / "big.py").exists()


def test_recovering_into_an_existing_unborn_worktree_on_that_branch_is_allowed(hub: Path) -> None:
    """`--abbrev-ref HEAD` says `HEAD` on an unborn branch, so the guard stated a falsehood."""
    wt = dirty_lane(hub, "wt-unborn-target")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    orphan = hub.parent / "wt-unborn-on"
    assert (
        git(
            "worktree", "add", "-q", "--orphan", "-b", "lane-unborn-on", str(orphan), cwd=hub
        ).returncode
        == 0
    )
    assert git("symbolic-ref", "--short", "HEAD", cwd=orphan).stdout.strip() == "lane-unborn-on"

    out = run_tool("recover", ref, "--into", str(orphan), "--branch", "lane-unborn-on", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "already exists on" not in out.stderr, out.stderr
    assert (orphan / "big.py").exists()


def test_a_filename_containing_a_carriage_return_round_trips(hub: Path) -> None:
    """`text=True` enables universal-newline translation: `\\r` in git's output became `\\n`.

    `surrogateescape` cannot help there — the byte decodes fine, it is the TEXT WRAPPER
    that rewrites it. So a path byte 0x0d either minted a phantom index entry that a bare
    commit then landed under a name existing nowhere on disk, or made a byte-perfect
    restore report "1 did NOT appear".
    """
    wt = dirty_lane(hub, "wt-cr")
    raw = b"caf\r.txt"
    name = os.fsdecode(raw)
    target = wt / name
    with open(target, "wb") as fh:
        fh.write(b"carriage return\n")
    assert git("add", "-A", cwd=wt).returncode == 0
    target.unlink()  # `AD`: the blob lives only in the index

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    ref = pin_ref(pinned.stdout)

    orphan = hub.parent / "wt-cr-orphan"
    assert git("worktree", "add", "--orphan", "-b", "lane-cr", str(orphan), cwd=hub).returncode == 0
    out = run_tool("recover", ref, "--into", str(orphan), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    index_bytes = subprocess.run(  # raw bytes
        ["git", "-C", str(orphan), "ls-files", "-z"], capture_output=True, check=False
    ).stdout
    assert raw in index_bytes.split(b"\0"), index_bytes
    assert b"caf\n.txt" not in index_bytes.split(b"\0"), "no CR-to-LF fold may be minted"


def test_a_worktree_whose_name_ends_in_a_carriage_return_can_be_pinned(hub: Path) -> None:
    """The same newline translation, one layer up: the worktree ROOT itself."""
    lane = hub.parent / "wt-cr-name\r"
    assert git("worktree", "add", "-q", str(lane), "-b", "lane-cr-name", cwd=hub).returncode == 0
    (lane / "big.py").write_text("the lane's work\n")

    pinned = run_tool("pin", cwd=lane)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    ref = pin_ref(pinned.stdout)

    assert destroy(hub, lane).returncode == 0
    out = run_tool("recover", ref, "--into", str(lane), "--branch", "lane-cr-name", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (lane / "big.py").read_text() == "the lane's work\n"


def test_a_pin_is_possible_in_a_worktree_whose_directory_name_is_non_utf8(hub: Path) -> None:
    """The lane KEY digests the path: a strict `.encode()` crashed `pin` with no ref.

    The round-17 fix made `_repo_root` decode the root with `surrogateescape`, and the
    repaired path then reached `lane_stash.lane_key`, whose `str(...).encode()` cannot
    encode a lone surrogate: `UnicodeEncodeError`, no ref written, exit 1 — which this tool
    reserves as "never a verdict about a lane's work".
    """
    lane = hub.parent / os.fsdecode(b"wt-lane\xe9")
    assert git("worktree", "add", "-q", str(lane), "-b", "lane-nonutf8", cwd=hub).returncode == 0
    (lane / "big.py").write_text("the lane's work\n")

    pinned = run_tool("pin", cwd=lane)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    assert "Traceback" not in pinned.stderr, pinned.stderr
    ref = pin_ref(pinned.stdout)

    listed = run_tool("list", "--path", str(lane), cwd=lane)
    assert listed.returncode == 0, listed.stdout + listed.stderr

    assert destroy(hub, lane).returncode == 0
    out = run_tool("recover", ref, "--into", str(lane), "--branch", "lane-nonutf8", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (lane / "big.py").read_text() == "the lane's work\n"


def test_recover_path_only_recreates_that_worktree_not_the_callers_checkout(hub: Path) -> None:
    """`--path <destroyed lane>` + no `--into` must NOT restore into the caller's checkout.

    It did: exit 0, the lane's uncommitted work landed in the hub (clean, at the pin's
    parent, so every guard passed), and the next bare commit would have put it on the
    caller's branch.
    """
    wt = dirty_lane(hub, "wt-pathonly")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    before = git("status", "--porcelain", cwd=hub).stdout
    assert destroy(hub, wt).returncode == 0

    out = run_tool("recover", ref, "--path", str(wt), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (wt / "big.py").exists(), "--path must recreate THAT worktree"
    assert git("status", "--porcelain", cwd=hub).stdout == before, (
        "the caller's checkout must be untouched"
    )
    assert not (hub / "big.py").exists()


def test_a_live_holder_whose_path_contains_a_newline_is_still_refused(hub: Path) -> None:
    """Porcelain v1 prints paths unquoted; `splitlines()` truncated the holder's path."""
    lane = hub.parent / "wt-holder-nl\nname"
    assert git("worktree", "add", "-q", str(lane), "-b", "lane-shared-nl", cwd=hub).returncode == 0
    (lane / "big.py").write_text("the holder's work\n")
    pinned = run_tool("pin", cwd=lane)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    ref = pin_ref(pinned.stdout)

    # The holder is LIVE, so a second worktree on that branch must be refused.
    out = run_tool(
        "recover",
        ref,
        "--into",
        str(hub.parent / "wt-holder-nl-new"),
        "--branch",
        "lane-shared-nl",
        cwd=hub,
    )
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "live worktree" in out.stderr, out.stderr
    assert not (hub.parent / "wt-holder-nl-new").exists()
    listing = git("worktree", "list", "--porcelain", cwd=hub).stdout
    assert listing.count("branch refs/heads/lane-shared-nl") == 1, listing


def test_a_tag_shadowing_the_branch_name_does_not_decide_the_guard(hub: Path) -> None:
    """A tag called like the branch wins git's ref priority; the guard must read the BRANCH.

    `pin`/`recover` named the branch through a SHORTENING read (`heads/lane` when ambiguous)
    and compared it against a BARE name, so the guard judged the TAG: a safe recovery was
    refused when the tag was older, and an advanced branch passed when the tag still sat at
    the pin's parent.
    """
    # an OLDER commit that the tag will point at, then the branch's own tip
    (hub / "older.txt").write_text("older\n")
    assert git("add", "older.txt", cwd=hub).returncode == 0
    assert git("commit", "-qm", "older", cwd=hub).returncode == 0
    older = git("rev-parse", "HEAD", cwd=hub).stdout.strip()
    (hub / "newer.txt").write_text("newer\n")
    assert git("add", "newer.txt", cwd=hub).returncode == 0
    assert git("commit", "-qm", "newer", cwd=hub).returncode == 0

    branch = "lane-wt-shadow"
    assert git("tag", branch, older, cwd=hub).returncode == 0

    wt = dirty_lane(hub, "wt-shadow")  # on `lane-wt-shadow`, at the newer commit
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    ref = pin_ref(pinned.stdout)
    # `pin` must name the branch it is ON, not a tag-shadowed shortening of it.
    assert f"--branch {branch}\n" in pinned.stdout, pinned.stdout
    assert "heads/" not in pinned.stdout, pinned.stdout

    # (a) the target still EXISTS and is on that branch: not a misroute.
    inplace = run_tool("recover", ref, "--into", str(wt), "--branch", branch, "--force", cwd=hub)
    assert inplace.returncode == 0, inplace.stdout + inplace.stderr

    # (b) the target is GONE and the branch is exactly at the pin's parent: safe.
    assert destroy(hub, wt).returncode == 0
    out = run_tool("recover", ref, "--into", str(wt), "--branch", branch, cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (wt / "big.py").exists()


def test_a_damaged_pin_cannot_take_list_down(hub: Path) -> None:
    """`list` is RECIPE step 1: one bad ref must not hide every pin (and exit 1)."""
    wt = dirty_lane(hub, "wt-damaged-list")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    good = pin_ref(pinned.stdout)

    # A pin-shaped commit whose tree is missing AND whose timestamp is out of range.
    bad_tree = "1111111111111111111111111111111111111111"
    raw = (
        f"tree {bad_tree}\n"
        f"parent {git('rev-parse', 'HEAD', cwd=hub).stdout.strip()}\n"
        "author lane-wip <l@w> 99999999999999 +0000\n"
        "committer lane-wip <l@w> 99999999999999 +0000\n"
        "\n"
        "lane-wip: damaged\n"
    )
    made = subprocess.run(
        ["git", "-C", str(hub), "hash-object", "-t", "commit", "-w", "--stdin"],
        input=raw.encode(),
        capture_output=True,
        check=False,
    )
    bad_sha = made.stdout.decode().strip()
    assert made.returncode == 0, made.stderr
    assert git("update-ref", "refs/pi-lane-wip/hub-damaged/1", bad_sha, cwd=hub).returncode == 0

    listed = run_tool("list", "--all", cwd=hub)
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert "Traceback" not in listed.stderr, listed.stderr
    assert good in listed.stdout or "wt-damaged-list" in listed.stdout


def test_a_recreated_empty_lane_directory_is_recoverable(hub: Path) -> None:
    """`mkdir -p` on the lane path must not turn a recovery into a subdirectory refusal.

    It did, and the remedy it printed was `--into <hub>` — restoring the lane's
    uncommitted work into the MAIN CHECKOUT, exit 0, ready for a bare commit.
    """
    ignore = hub / ".gitignore"
    ignore.write_text(ignore.read_text() + ".worktrees/\n")
    assert git("add", ".gitignore", cwd=hub).returncode == 0
    assert git("commit", "-qm", "ignore lane dir", cwd=hub).returncode == 0

    lane_dir = hub / ".worktrees" / "wt-recreated"
    lane_dir.parent.mkdir(exist_ok=True)
    assert (
        git("worktree", "add", "-q", str(lane_dir), "-b", "lane-recreated", cwd=hub).returncode == 0
    )
    (lane_dir / "work.py").write_text("the lane's work\n")
    pinned = run_tool("pin", cwd=lane_dir)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    assert destroy(hub, lane_dir).returncode == 0
    lane_dir.mkdir()  # a lane that recreates its own path before recovering

    before = git("status", "--porcelain", cwd=hub).stdout
    out = run_tool("recover", ref, "--into", str(lane_dir), "--branch", "lane-recreated", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (lane_dir / "work.py").read_text() == "the lane's work\n"
    assert git("status", "--porcelain", cwd=hub).stdout == before, "the hub must be untouched"


def test_the_branch_is_recreated_even_when_a_tag_shadows_its_name(hub: Path) -> None:
    """The "branch is gone" probe must be qualified, or a tag suppresses the recreate."""
    wt = dirty_lane(hub, "wt-tag-recreate")
    branch = "lane-wt-tag-recreate"
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    # The branch is gone; a tag of the same name survives.
    assert git("branch", "-D", branch, cwd=hub).returncode == 0
    assert git("tag", branch, "HEAD", cwd=hub).returncode == 0

    out = run_tool("recover", ref, "--into", str(wt), "--branch", branch, cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    # The FULL ref: `--short` shortens to `heads/<n>` while a tag shadows the name.
    assert (
        git("symbolic-ref", "--quiet", "HEAD", cwd=wt).stdout.strip() == f"refs/heads/{branch}"
    ), "the worktree must be ON the recreated branch, not detached"
    assert (wt / "big.py").exists()


def test_a_benign_git_warning_does_not_claim_paths_were_dropped(hub: Path) -> None:
    """git exits 0 with `warning:`/`hint:` on stderr for a nested repo or a CRLF file."""
    wt = dirty_lane(hub, "wt-benign")
    nested = wt / "vendored"
    nested.mkdir()
    assert git("init", "-q", ".", cwd=nested).returncode == 0
    (nested / "inner.py").write_text("inner\n")
    assert git("add", "inner.py", cwd=nested).returncode == 0
    assert (
        git(
            "-c", "user.email=a@b.c", "-c", "user.name=a", "commit", "-qm", "inner", cwd=nested
        ).returncode
        == 0
    )

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    assert "could NOT be added" not in pinned.stderr, pinned.stderr
    assert "`chmod`" not in pinned.stderr, pinned.stderr
    # The nested repo IS announced, by the code that knows what it means.
    assert "nested repo" in pinned.stderr.lower(), pinned.stderr


def test_the_only_unindexable_content_is_not_reported_as_clean(hub: Path) -> None:
    """`unreadable and worktree_clean` is the ONLY guard between a lane's sole uncapturable
    content and the false verdict "clean — nothing to pin" (exit 0).

    Removing that `raise` left the whole suite green, so it was the one guard in `snapshot()`
    with no discriminating test.
    """
    wt = clean_lane(hub, "wt-unreadable-only")
    blocked = wt / "rootish.bin"
    blocked.write_text("the lane's only work\n")
    blocked.chmod(0o000)
    try:
        assert git("status", "--porcelain", cwd=wt).stdout.strip() == "?? rootish.bin"
        out = run_tool("pin", cwd=wt)
        assert out.returncode == 2, out.stdout + out.stderr
        assert "could NOT be added" in out.stderr, out.stderr
        assert "looking complete" in out.stderr, out.stderr
        assert "clean — nothing to pin" not in out.stdout, out.stdout
        assert (
            git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub).stdout.strip()
            == ""
        ), "no ref may be minted over work the pin could not hold"
    finally:
        with contextlib.suppress(FileNotFoundError):
            blocked.chmod(0o600)


def test_an_unreadable_holder_listing_refuses_instead_of_failing_open(
    hub: Path, git_shim: Path
) -> None:
    """A failed `worktree list` is NOT "no holder": `-f` then puts two live worktrees on it.

    Measured with a shim failing only `worktree list`: exit 0, "created worktree … on
    shared", and two live worktrees on one branch afterwards — where a commit in either
    rewinds the other's base.
    """
    holder = hub.parent / "wt-ho-shared"
    assert (
        git("worktree", "add", "-q", str(holder), "-b", "lane-ho-shared", cwd=hub).returncode == 0
    )
    (holder / "holder.py").write_text("the holder's work\n")

    wt = dirty_lane(hub, "wt-ho")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = _run_tool_with_shim(
        git_shim,
        "LW_FAIL_WORKTREE_LIST",
        "recover",
        ref,
        "--into",
        str(hub.parent / "wt-ho-new"),
        "--branch",
        "lane-ho-shared",
        cwd=hub,
    )
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "cannot be ruled out" in out.stderr, out.stderr
    assert not (hub.parent / "wt-ho-new").exists(), "no second worktree may be created"
    listing = git("worktree", "list", "--porcelain", cwd=hub).stdout
    assert listing.count("branch refs/heads/lane-ho-shared") == 1, listing


def test_a_detached_existing_target_is_not_silently_restored_under_branch(hub: Path) -> None:
    """`current = "HEAD"` is what refuses a DETACHED target; with it dropped, `--branch` is
    silently ignored and the next bare commit lands on a detached HEAD (reachable from no
    branch). No test presented a detached target before."""
    wt = dirty_lane(hub, "wt-detached-target")
    branch = "lane-wt-detached-target"
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    target = hub.parent / "wt-detached-into"
    assert git("worktree", "add", "-q", "--detach", str(target), "HEAD", cwd=hub).returncode == 0
    assert git("symbolic-ref", "-q", "HEAD", cwd=target).returncode != 0, "must be detached"

    out = run_tool("recover", ref, "--into", str(target), "--branch", branch, cwd=hub)
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "does not switch an existing worktree" in out.stderr, out.stderr
    assert git("symbolic-ref", "-q", "HEAD", cwd=target).returncode != 0, "still detached"
    assert git("status", "--porcelain", cwd=target).stdout.strip() == ""


def test_the_pin_id_ceiling_is_a_refusal_not_a_phantom_pin(hub: Path) -> None:
    """Without the final `raise`, `pin_ref` returns None and `pin` announces a phantom ref."""
    wt = dirty_lane(hub, "wt-full")
    # Learn the lane key from a first pin, then fill the registry and delete the pin's ref.
    first = run_tool("pin", cwd=wt)
    assert first.returncode == 0, first.stderr
    ref = pin_ref(first.stdout)
    assert git("update-ref", "-d", ref, cwd=hub).returncode == 0
    key = ref.rsplit("/", 2)[1]
    for n in range(1, 51):
        assert git("update-ref", f"refs/pi-lane-wip/{key}/{n}", "HEAD", cwd=hub).returncode == 0

    out = run_tool("pin", cwd=wt)
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "already taken" in out.stderr, out.stderr
    assert "ref    None" not in out.stdout, out.stdout
    assert git("rev-parse", "--verify", "-q", f"refs/pi-lane-wip/{key}/51", cwd=hub).returncode != 0


def test_a_qualified_branch_name_lands_on_that_branch(hub: Path) -> None:
    """`--branch refs/heads/x` must mean the branch `x`, not a doubly-qualified one.

    It created `refs/heads/refs/heads/x` at exit 0 — a branch no tool will ever look at — and
    with the branch still alive it detached and left the worktree occupying the path, so the
    correctly-spelled retry was refused. `git worktree list --porcelain` and
    `git for-each-ref` print that qualified form, so a lane can copy it.
    """
    wt = dirty_lane(hub, "wt-qualified")
    branch = "lane-wt-qualified"
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    # (a) the branch SURVIVES the removal: it must be checked out, not detached.
    assert destroy(hub, wt).returncode == 0
    out = run_tool("recover", ref, "--into", str(wt), "--branch", f"refs/heads/{branch}", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert git("symbolic-ref", "--quiet", "HEAD", cwd=wt).stdout.strip() == f"refs/heads/{branch}"
    assert (wt / "big.py").exists()

    # (b) the branch is GONE: it must be recreated under its own name, not a doubled one.
    assert destroy(hub, wt).returncode == 0
    assert git("branch", "-D", branch, cwd=hub).returncode == 0
    gone = run_tool("recover", ref, "--into", str(wt), "--branch", f"refs/heads/{branch}", cwd=hub)
    assert gone.returncode == 0, gone.stdout + gone.stderr
    heads = git("for-each-ref", "--format=%(refname)", "refs/heads/", cwd=hub).stdout.split()
    assert f"refs/heads/{branch}" in heads, heads
    assert not [h for h in heads if h.count(branch) > 1], heads


def test_a_non_branch_ref_is_refused_with_a_remedy(hub: Path) -> None:
    """`--branch refs/tags/x` is a state error, not something to hand to `worktree add`."""
    wt = dirty_lane(hub, "wt-nonbranch")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert git("tag", "some-tag", "HEAD", cwd=hub).returncode == 0

    out = run_tool("recover", ref, "--into", str(wt), "--branch", "refs/tags/some-tag", cwd=hub)
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "names a non-branch ref" in out.stderr, out.stderr


def test_an_unreadable_index_flags_read_refuses_instead_of_trusting_status(
    hub: Path, git_shim: Path
) -> None:
    """`ls-files -v` is the LAST defence for content `git status` cannot see.

    A mutation of its failure branch (raise -> `return False`) leaves the suite green, and
    the consequence is A-class: a target whose only local work is an assume-unchanged
    modification is overwritten with no `--force` and reports success.
    """
    wt = dirty_lane(hub, "wt-flags-read")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    target = hub.parent / "wt-flags-target"
    assert git("worktree", "add", "-q", str(target), "-b", "lane-flags", cwd=hub).returncode == 0
    precious = "THE TARGET'S OWN PRECIOUS WORK\n"
    (target / "tracked.txt").write_text(precious)
    assert git("update-index", "--assume-unchanged", "tracked.txt", cwd=target).returncode == 0
    assert git("status", "--porcelain", cwd=target).stdout.strip() == "", "invisible to status"

    out = _run_tool_with_shim(
        git_shim, "LW_FAIL_LS_FILES", "recover", ref, "--into", str(target), cwd=hub
    )
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    # The GUARD's own refusal, not git's `read-tree` backstop: an unreadable flags list must
    # be converted into a refusal that names ITSELF, so the lane learns the state is unknown
    # rather than "the restore failed".
    assert "could not read the index flags" in out.stderr, out.stderr
    assert (target / "tracked.txt").read_text() == precious, "the target's work must survive"


def test_a_scratch_write_tree_that_exits_zero_with_error_noise_is_a_refusal(
    hub: Path, git_shim: Path
) -> None:
    """`write-tree` exits 0 while printing `error: cache entry has null sha1:`.

    Keying on the return code alone pinned the EMPTY tree as the lane's working state and
    exited 0 ("pinned 0 file(s)"). The `_index_tree` twin has had this test for rounds; the
    scratch twin did not.
    """
    wt = dirty_lane(hub, "wt-scratch-noise")
    out = _run_tool_with_shim(git_shim, "LW_SCRATCH_WRITE_NOISE", "pin", cwd=wt)
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "trustworthy tree" in out.stderr, out.stderr
    assert (
        git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub).stdout.strip()
        == ""
    ), "a garbled tree must not be pinned"


def test_a_failed_head_read_is_not_an_unborn_head(hub: Path, git_shim: Path) -> None:
    """A failed `HEAD` read must not wipe the index and stage a whole-tree revert.

    `_out(..., check=False)` yielded "" for BOTH "unborn" and "the read failed", so the
    index was emptied and the pin's staged DELTA applied to it — the round-9 P1 — and the
    lane's next bare commit DELETED committed files off the branch, at exit 0.
    """
    wt = dirty_lane(hub, "wt-headread")
    branch = "lane-wt-headread"
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = _run_tool_with_shim(
        git_shim,
        "LW_FAIL_REV_HEAD",
        "recover",
        ref,
        "--into",
        str(wt),
        "--branch",
        branch,
        cwd=hub,
    )
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "could not read HEAD" in out.stderr, out.stderr
    # Nothing may be restored over a state we could not read.


def test_a_failed_second_parent_read_is_not_a_pin_without_a_split(
    hub: Path, git_shim: Path
) -> None:
    """A failed `^2` read must not be reported as "this pin recorded no staged half".

    Measured: exit 0, no "with the staged/unstaged split" clause and no warning, and a file
    the lane had STAGED came back untracked — so its next commit committed nothing.
    """
    wt = dirty_lane(hub, "wt-2ndread")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    # ⛔ ARM `LW_FAIL_REV_SECOND`, NOT `LW_FAIL_REV_HEAD`. Arming the HEAD arm made this a
    # TAUTOLOGY: the unstage step fails first, so `rc == 3` came from a different guard and
    # the `^2` guard could be deleted with this test still green (measured: a mutant with the
    # `^2` read reverted to `check=False` PASSED). Only the `^2` read is failed here.
    out = _run_tool_with_shim(
        git_shim,
        "LW_FAIL_REV_SECOND",
        "recover",
        ref,
        "--into",
        str(hub.parent / "wt-2nd-new"),
        cwd=hub,
    )
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "could not read" in out.stderr and "^2" in out.stderr, out.stderr
    # And the recovery must NOT have completed: a dropped staged half means an `AM` file
    # comes back untracked, so the pin's split is silently lost.
    made = hub.parent / "wt-2nd-new"
    assert not made.exists() or not list(made.iterdir()), "the recovery ran over an unread state"


def test_a_failed_branch_tip_read_does_not_skip_the_stale_guard(hub: Path, git_shim: Path) -> None:
    """A failed tip read must not let an old pin be built on an advanced branch silently."""
    wt = dirty_lane(hub, "wt-tipread")
    branch = "lane-wt-tipread"
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    # advance the branch after the pin
    (wt / "landed.py").write_text("landed\n")
    assert git("add", "landed.py", cwd=wt).returncode == 0
    assert git("commit", "-qm", "landed", cwd=wt).returncode == 0
    assert destroy(hub, wt).returncode == 0

    out = _run_tool_with_shim(
        git_shim,
        "LW_FAIL_REV_TIP",
        "recover",
        ref,
        "--into",
        str(wt),
        "--branch",
        branch,
        cwd=hub,
    )
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "could not read" in out.stderr, out.stderr


def test_a_failed_target_head_read_still_runs_the_at_head_guard(
    hub: Path, git_shim: Path, tmp_path: Path
) -> None:
    """A target whose `HEAD` read FAILED is not a target "at the pinned commit".

    `_out(..., check=False)` gave "" for both, so the at-HEAD guard at the existing target
    was SKIPPED and the restore ran on top of a target that had committed SINCE the pin —
    mixing the pin's tree with newer commits at exit 0, the very thing the guard exists to
    refuse. Only the FIRST `HEAD` probe fails here, so the later unstage probe succeeds:
    without the guard the recovery COMPLETES.
    """
    # CLEAN target, so the DIRTY guard cannot be the thing that refuses: the guard under
    # test is the existing-target at-HEAD one, and a dirty target would mask it.
    wt = clean_lane(hub, "wt-exist-head")
    (wt / "tracked.txt").write_text("v2 — the lane's uncommitted work\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    # The lane commits after the pin: target is CLEAN and PAST the pin's parent.
    (wt / "landed.py").write_text("landed\n")
    assert git("add", "-A", cwd=wt).returncode == 0
    assert git("commit", "-qm", "landed", cwd=wt).returncode == 0
    assert not (git("status", "--porcelain", cwd=wt).stdout or "").strip(), (
        "the target must be clean for this guard to be reachable"
    )
    before = (wt / "tracked.txt").read_text()

    out = _run_tool_with_shim(
        git_shim,
        "LW_FAIL_REV_HEAD_ONCE",
        "recover",
        ref,
        "--into",
        str(wt),
        cwd=hub,
        value=str(tmp_path / "head-once"),
    )
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "could not read HEAD" in out.stderr, out.stderr
    # NOTHING may be restored over a target whose state we could not read.
    assert (wt / "tracked.txt").read_text() == before, "the target's work tree was touched"


def test_a_failed_tree_probe_is_a_read_failure_not_a_damage_verdict(
    hub: Path, git_shim: Path
) -> None:
    """A FAILED `cat-file -e` read must not render as the fact "the pin is damaged".

    Round 26 fixed the ordering (the staged half must be consulted before calling a pin
    damaged) but the probe's FAILURE was still read as "the object is absent". A transient
    read failure therefore printed a definite verdict about the pin — "DAMAGED … may still be
    restorable from another clone" — over an intact pin. Same three-way contract as
    `_verify_opt`: 0 = present, git's own "not a valid object name" = absent, anything else
    raises. (A GENUINELY missing object is covered by
    `test_a_pin_whose_worktree_tree_is_gone_names_the_surviving_staged_half`.)
    """
    wt = dirty_lane(hub, "wt-tree-probe")
    # A pin carries a staged half only when something was STAGED; without one there is no
    # `^2` to salvage and "damaged" is the correct verdict.
    (wt / "half.py").write_text("staged half\n")
    assert git("add", "half.py", cwd=wt).returncode == 0
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = _run_tool_with_shim(
        git_shim, "LW_FAIL_CAT_FILE", "recover", ref, "--into", str(wt), cwd=hub
    )
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "could not read" in out.stderr, out.stderr
    # A read failure is NOT a verdict about the pin.
    assert "is damaged" not in out.stderr, out.stderr
    assert "DAMAGED" not in out.stderr, out.stderr


def test_an_unreadable_DIRECTORY_is_not_reported_as_clean(hub: Path) -> None:
    """A mode-000 DIRECTORY exits `git add -A --ignore-errors` with rc 0 and only a `warning:`.

    The classifier keyed on `error:` lines alone, so the directory was invisible: `pin`
    printed **"clean — nothing to pin"** (exit 0) over the lane's only work — the false "your
    work is safe" verdict, after which the lane lets its worktree be removed and the content
    is gone. A directory needs `chmod +r AND +x` to be readable, so this is not exotic.
    """
    wt = clean_lane(hub, "wt-unreadable-dir")
    blocked = wt / "feature"
    blocked.mkdir()
    (blocked / "work.py").write_text("the lane's only work\n")
    blocked.chmod(0o000)
    try:
        out = run_tool("pin", cwd=wt)
        assert out.returncode == 2, out.stdout + out.stderr
        assert "clean — nothing to pin" not in out.stdout, out.stdout
        assert "could NOT be added" in out.stderr, out.stderr
        assert "could not open directory" in out.stderr, out.stderr
        assert (
            git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub).stdout.strip()
            == ""
        ), "no ref may be minted over work the pin could not hold"
    finally:
        with contextlib.suppress(FileNotFoundError):
            blocked.chmod(0o700)


def test_a_partially_pinned_unreadable_directory_is_announced(hub: Path) -> None:
    """With OTHER dirty work the pin is legitimate — but it must SAY what it could not hold.

    Measured: `pinned 4 file(s)`, **stderr empty**, and the ref held only the readable paths;
    `recover` then printed `recovered 4 file(s)` over a pin missing the lane's actual work.
    A short pin announced as complete is the class-B failure this whole file is about.
    """
    wt = dirty_lane(hub, "wt-unreadable-dir2")
    blocked = wt / "feature"
    blocked.mkdir()
    (blocked / "work.py").write_text("the lane's only work\n")
    blocked.chmod(0o000)
    try:
        out = run_tool("pin", cwd=wt)
        assert out.returncode == 0, out.stdout + out.stderr
        assert "pinned " in out.stdout and "file(s)" in out.stdout, out.stdout
        assert "could NOT be added" in out.stderr, out.stderr
        assert "could not open directory" in out.stderr, out.stderr
    finally:
        with contextlib.suppress(FileNotFoundError):
            blocked.chmod(0o700)


def test_a_failed_toplevel_read_does_not_skip_the_nested_guard(
    hub: Path, git_shim: Path, tmp_path: Path
) -> None:
    """A FAILED `rev-parse --show-toplevel` is not "there is no enclosing worktree".

    `_worktree_root` returned `""` for both, so the nested-worktree guard was skipped and
    `recover --into <peer>/newsub` CREATED a nested worktree at exit 0 — the peer's next
    `git add -A` then records the recovered work as a mode-160000 gitlink, which is the harm
    that guard's own message names. Same fail-closed policy as `_branch_holder`.
    """
    peer = dirty_lane(hub, "wt-peer")
    wt = dirty_lane(hub, "wt-toplevel")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    nested = peer / "newsub"
    shim = _toplevel_failing_shim(git_shim, tmp_path)
    out = _run_tool_with_shim(
        shim, "LW_FAIL_TOPLEVEL_ONCE", "recover", ref, "--into", str(nested), cwd=hub
    )
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "could not read the worktree root" in out.stderr, out.stderr
    assert not nested.exists(), "a nested worktree was created over an unread state"


def test_an_unreadable_target_subdirectory_is_a_refusal_not_a_traceback(hub: Path) -> None:
    """`target.iterdir()` on a mode-0111 directory raised an UNCAUGHT `PermissionError`.

    Exit 1 with a traceback — the code this module reserves for "never a verdict about a
    lane's work", on the RECOVERY path, whose whole job is to hand the lane a remedy.
    """
    wt = dirty_lane(hub, "wt-blocked-sub")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    blocked = wt / "blocked"
    blocked.mkdir()
    blocked.chmod(0o111)  # reachable, not listable
    try:
        out = run_tool("recover", ref, "--into", str(blocked), cwd=hub)
        assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
        assert "Traceback" not in out.stderr, out.stderr
        assert "cannot be read" in out.stderr, out.stderr
        assert "--into" in out.stderr, out.stderr
    finally:
        with contextlib.suppress(FileNotFoundError):
            blocked.chmod(0o700)


def _toplevel_failing_shim(shim: Path, tmp_path: Path) -> Path:
    """A shim that fails `rev-parse --show-toplevel` on its SECOND call in a run.

    The FIRST read belongs to `_repo_root()` at startup — failing it refuses before the guard
    is reached, so the guard would never be exercised (measured: rc 2, "not inside a git work
    tree — nothing to pin", a message about neither the guard nor the target).
    """
    script = shim / "git"
    body = script.read_text()
    marker = "  rev-parse)\n"
    assert marker in body
    body = body.replace(
        marker,
        marker
        + """    if [ -n "$LW_FAIL_TOPLEVEL_ONCE" ]; then
      for a in "$@"; do
        if [ "$a" = "--show-toplevel" ]; then
          if [ -e "$LW_FAIL_TOPLEVEL_ONCE" ]; then
            rm -f "$LW_FAIL_TOPLEVEL_ONCE"
            echo "shim: refusing the SECOND rev-parse --show-toplevel" >&2; exit 128
          else
            : > "$LW_FAIL_TOPLEVEL_ONCE"
          fi
        fi
      done
    fi
""",
        1,
    )
    script.write_text(body)
    script.chmod(0o755)
    return shim


def test_an_unreadable_index_is_reported_not_a_traceback(hub: Path) -> None:
    """`shutil.copy2` on a mode-000 INDEX raised `PermissionError` → exit 1, no pin at all.

    `is_file()` stats fine without a read bit, so the copy was the first read. The caller
    already has a loud degradation for "the staged half is not captured" (the warning plus
    the `worktree_clean` refusal); the traceback bypassed it entirely — 0 refs written, the
    primary verb dead, and a lane that sees a traceback instead of a pin lets its worktree
    be removed.
    """
    wt = dirty_lane(hub, "wt-badindex")
    index_path = Path(
        git("rev-parse", "--path-format=absolute", "--git-path", "index", cwd=wt).stdout.strip()
    )
    assert index_path.is_file(), index_path
    index_path.chmod(0o000)
    try:
        out = run_tool("pin", cwd=wt)
        assert out.returncode == 0, f"{out.returncode}: {out.stdout}{out.stderr}"
        assert "Traceback" not in out.stderr, out.stderr
        assert "pinned " in out.stdout, out.stdout
        # The degradation is LOUD: the staged half is not in this pin.
        assert "STAGED" in out.stderr or "staged" in out.stderr, out.stderr
    finally:
        with contextlib.suppress(FileNotFoundError):
            index_path.chmod(0o600)


def test_a_symlink_loop_in_the_target_is_a_state_error(hub: Path, tmp_path: Path) -> None:
    """`Path.resolve()` raises `RuntimeError` (not `OSError`) on a symlink loop → exit 1.

    A lane path replaced by a stale symlink loop is exactly what `--path` is used on once a
    lane is already confused, and the tool answered with a crash instead of a remedy.
    """
    loopdir = tmp_path / "loopdir"
    loopdir.mkdir()
    (loopdir / "a").symlink_to(loopdir / "b")
    (loopdir / "b").symlink_to(loopdir / "a")
    out = run_tool("pin", "--path", str(loopdir / "a"), cwd=hub)
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "Traceback" not in out.stderr, out.stderr
    assert "cannot be resolved" in out.stderr, out.stderr


def test_a_failed_head_read_during_pin_is_not_an_unborn_history(hub: Path, git_shim: Path) -> None:
    """`snapshot`'s HEAD read: rc != 0 was rendered as "this repo has no commits".

    The remedy it printed ("commit once, then pin") is wrong and non-actionable on a repo
    that HAS commits, and no pin was written — a failed read turned into a fact about the
    lane's history. `_verify_opt` is the drop-in.
    """
    wt = dirty_lane(hub, "wt-pin-head")
    out = _run_tool_with_shim(git_shim, "LW_FAIL_EXACT_HEAD", "pin", cwd=wt)
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "no HEAD commit" not in out.stderr, out.stderr
    assert "could not read HEAD" in out.stderr, out.stderr


def test_a_failed_branch_read_is_not_a_detached_pin(hub: Path, git_shim: Path) -> None:
    """A FAILED `symbolic-ref` was recorded as the fact "detached" — and printed a remedy
    that detaches.

    Measured: `pin` reported `branch HEAD` and its `recover … --into <path>` (no `--branch`)
    left the worktree detached at the pin's parent, so the lane's next commit landed on no
    branch at all. The pin is intact; the MISPLACEMENT is the harm.
    """
    wt = dirty_lane(hub, "wt-branch-read")
    out = _run_tool_with_shim(git_shim, "LW_FAIL_SYMBOLIC_REF", "pin", cwd=wt)
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "could not read the branch" in out.stderr, out.stderr
    assert "branch HEAD" not in out.stdout, out.stdout


def test_a_failed_per_ref_read_does_not_render_a_pin_as_dangling(hub: Path, git_shim: Path) -> None:
    """`list` printed "DANGLING: the ref names no object; it holds no work" for a READ failure.

    That is the one conclusion `_describe` forbids ever rendering wrongly — it is what makes a
    lane stop looking — and it sits on RECIPE step 1.
    """
    wt = dirty_lane(hub, "wt-describe")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    out = _run_tool_with_shim(git_shim, "LW_FAIL_VERIFY_QUIET_REF", "list", "--all", cwd=hub)
    assert out.returncode == 0, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert ref in out.stdout, out.stdout
    assert "DANGLING" not in out.stdout, out.stdout
    assert "UNKNOWN" in out.stdout, out.stdout


def test_a_refusal_does_not_delete_the_target_directory(hub: Path, tmp_path: Path) -> None:
    """`rmdir` ran BEFORE the guards, so a REFUSAL could already have removed the directory.

    An unreadable/undeletable directory also produced an unwrapped `PermissionError`
    (exit 1 + traceback) instead of a state error with a remedy.
    """
    wt = dirty_lane(hub, "wt-rmdir")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    # An EMPTY directory inside a peer worktree that does NOT ignore it: the nested guard
    # must refuse — and the directory must still be there afterwards.
    peer = dirty_lane(hub, "wt-rmdir-peer")
    empty = peer / "notignored"
    empty.mkdir()
    before = git("worktree", "list", "--porcelain", cwd=hub).stdout
    out = run_tool("recover", ref, "--into", str(empty), cwd=hub)
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert empty.is_dir(), "a refusal deleted the caller's directory"
    assert git("worktree", "list", "--porcelain", cwd=hub).stdout == before


def test_a_moved_live_holder_is_not_treated_as_dead(hub: Path) -> None:
    """`mv` keeps the OLD path registered, so "path missing" must not mean "holder dead".

    `git worktree list` cannot tell a moved-but-live holder from a removed one (both read
    `prunable`), so `-f` over a missing path put TWO live worktrees on `shared`: measured,
    the moved-but-live worktree then reported the other lane's landed commit as a STAGED
    DELETION, and its next bare commit would revert reviewed work.
    """
    holder = clean_lane(hub, "wt-mv-holder")
    branch = "lane-wt-mv-holder"
    (holder / "w.txt").write_text("holder work\n")
    pinned = run_tool("pin", cwd=holder)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    moved = hub.parent / "wt-mv-holder-moved"
    holder.rename(moved)  # plain `mv`: the old path stays registered
    try:
        target = hub.parent / "wt-mv-new"
        out = run_tool("recover", ref, "--into", str(target), "--branch", branch, cwd=hub)
        assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
        assert "EITHER a dead record" in out.stderr, out.stderr
        assert not target.exists(), "a second worktree was created on a LIVE branch"
        assert git("worktree", "list", "--porcelain", cwd=hub).stdout.count(branch) == 1
    finally:
        moved.rename(holder)


def test_a_force_added_ignored_path_is_in_the_pinned_WORKTREE(
    hub: Path,
) -> None:
    """`git add -f` of an IGNORED path must reach the worktree tree, not only the index half.

    The scratch index was built with `read-tree HEAD`, and `add -A` honours ignore rules for
    anything not already in that index — so the force-added path never reached the worktree
    tree. Measured: `pin` said "pinned 3 file(s)" over a 4-path state, and `recover` printed
    `recovered 3 file(s) … with the staged/unstaged split` while the file was ABSENT from
    disk; the lane's next `commit -a` then recorded a deletion of its own recovered change.
    """
    wt = clean_lane(hub, "wt-forceadd")
    # A rule that is NOT already in the hub's `.gitignore` (a re-write of the same text is
    # no change, so the commit below would be empty).
    with (wt / ".gitignore").open("a") as fh:
        fh.write("forcelocal/\n")
    assert git("add", ".gitignore", cwd=wt).returncode == 0
    assert git("commit", "-qm", "ignore", cwd=wt).returncode == 0
    (wt / "forcelocal").mkdir()
    (wt / "forcelocal" / "local.conf").write_text("the lane's secret config\n")
    assert git("add", "-f", "forcelocal/local.conf", cwd=wt).returncode == 0

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    ref = pin_ref(pinned.stdout)
    sha = pinned.stdout.split("sha")[1].split()[0]
    tree_paths = git("ls-tree", "-r", "--name-only", sha, cwd=wt).stdout.split()
    assert "forcelocal/local.conf" in tree_paths, tree_paths

    assert destroy(hub, wt).returncode == 0
    out = run_tool("recover", ref, "--into", str(wt), "--branch", "lane-wt-forceadd", cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    restored = wt / "forcelocal" / "local.conf"
    assert restored.is_file(), "the force-added ignored path was not restored to disk"
    assert restored.read_text() == "the lane's secret config\n"


def test_a_non_repository_path_is_not_reported_as_a_missing_pin(hub: Path) -> None:
    """`recover --path <not a repo>` said "'<ref>' does not name a commit".

    The pin exists, is intact, and recovers fine from the same repo — the message told a
    lane whose worktree was already gone the one thing this file forbids saying wrongly.
    """
    wt = dirty_lane(hub, "wt-nonrepo")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    not_repo = hub.parent / "wt-nonrepo-plain"
    not_repo.mkdir()
    out = run_tool("recover", ref, "--path", str(not_repo), "--into", str(not_repo / "x"), cwd=hub)
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "does not name a commit" not in out.stderr, out.stderr
    assert "not a git work tree" in out.stderr, out.stderr


def test_a_failed_staged_tree_read_is_not_reported_as_pin_damage(hub: Path, git_shim: Path) -> None:
    """A FAILED `^2^{tree}` read was reported as "its TREE object is unreadable" (pin damage).

    Measured: the tool then printed a salvage command that PROVED the pin was never damaged,
    and the promised staged/unstaged split was skipped — the pinned `A half.py` came back
    `?? half.py`. A transient read failure must not render as a fact about the pin.
    """
    wt = dirty_lane(hub, "wt-staged-tree")
    (wt / "half.py").write_text("staged half\n")
    assert git("add", "half.py", cwd=wt).returncode == 0
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = _run_tool_with_shim(
        git_shim, "LW_FAIL_STAGED_TREE", "recover", ref, "--into", str(wt), cwd=hub
    )
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "could not read" in out.stderr, out.stderr
    assert "unreadable" not in out.stderr, out.stderr


def test_an_assume_unchanged_path_IS_pinned_from_the_worktree(hub: Path) -> None:
    """The round-28 index seeding must NOT inherit `assume-unchanged`/`skip-worktree`.

    `git add -A` SKIPS flagged entries, so seeding the scratch index from a copy of the real
    index made the worktree view STALE for exactly those paths — measured: `pin` printed
    "clean — nothing to pin" (no ref at all) over the lane's only work, and with another
    dirty file it pinned the HEAD content and `recover` then reported success over the loss.
    This is #10812 reached through the tool's own verbs, with a success message at both ends.
    """
    for flag, tag in (("--assume-unchanged", "assume-unchanged"), ("--skip-worktree", "skip")):
        name = f"wt-flag-{tag}"
        wt = clean_lane(hub, name)
        branch = f"lane-{name}"
        original = (wt / "tracked.txt").read_text()
        assert git("update-index", flag, "tracked.txt", cwd=wt).returncode == 0
        (wt / "tracked.txt").write_text(f"the lane's ONLY work ({tag})\n")
        try:
            out = run_tool("pin", cwd=wt)
            assert out.returncode == 0, f"{tag}: {out.stdout}{out.stderr}"
            assert "clean — nothing to pin" not in out.stdout, f"{tag}: {out.stdout}"
            ref = pin_ref(out.stdout)
            sha = out.stdout.split("sha")[1].split()[0]
            pinned = git("show", f"{sha}:tracked.txt", cwd=hub).stdout
            assert pinned == f"the lane's ONLY work ({tag})\n", f"{tag}: pinned={pinned!r}"

            assert destroy(hub, wt).returncode == 0
            back = run_tool("recover", ref, "--into", str(wt), "--branch", branch, cwd=hub)
            assert back.returncode == 0, f"{tag}: {back.stdout}{back.stderr}"
            assert (wt / "tracked.txt").read_text() == f"the lane's ONLY work ({tag})\n"
        finally:
            with contextlib.suppress(Exception):
                git("update-index", "--no-assume-unchanged", "tracked.txt", cwd=wt)
                git("update-index", "--no-skip-worktree", "tracked.txt", cwd=wt)
                (wt / "tracked.txt").write_text(original)
    assert True


def test_a_recreated_lane_directory_keeps_ITS_OWN_lane_key(hub: Path) -> None:
    """`list --path <hub>/.worktrees/<lane>` must not report the HUB's key.

    Normalising every path to its enclosing worktree root broke the shape this tool exists
    for: after `worktree remove --force` the lane recreates its own path with `mkdir -p`, and
    that directory is inside the HUB — so its key became the hub's and `list` answered "lane
    <hub-key> has no wip pin" while the lane's pins sat one ref away (measured). The false
    "the work is gone" verdict, in the state the RECIPE describes.
    """
    # ⛔ THE FLEET LAYOUT, or the test proves nothing: every lane lives INSIDE the hub at
    # `<hub>/.worktrees/<lane>` and `.worktrees/` is gitignored there, which is exactly why
    # "normalise to the enclosing worktree root" turned a lane into the HUB.
    assert git("checkout", "-q", "-B", "main", cwd=hub).returncode == 0
    with (hub / ".gitignore").open("a") as fh:
        fh.write(".worktrees/\n")
    assert git("add", ".gitignore", cwd=hub).returncode == 0
    assert git("commit", "-qm", "ignore lanes", cwd=hub).returncode == 0
    (hub / ".worktrees").mkdir(exist_ok=True)
    lane = hub / ".worktrees" / "wt-recreated"
    made = git("worktree", "add", "-q", str(lane), "-b", "lane-wt-recreated", cwd=hub)
    assert made.returncode == 0, made.stderr
    (lane / "tracked.txt").write_text("the lane's work\n")
    pinned = run_tool("pin", cwd=lane)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, lane).returncode == 0

    lane.mkdir()  # the lane recreates its own path, empty
    try:
        out = run_tool("list", "--path", str(lane), cwd=hub)
        assert out.returncode == 0, out.stderr
        assert ref in out.stdout, f"list --path did not find the lane's own pin: {out.stdout}"
        assert "has no wip pin" not in out.stdout, out.stdout
    finally:
        with contextlib.suppress(OSError):
            lane.rmdir()


def test_a_failed_inside_work_tree_read_is_not_a_verdict(hub: Path, git_shim: Path) -> None:
    """A FAILED `--is-inside-work-tree` read rendered as "not a git work tree" (rc 2).

    That is a definite statement about a live worktree — at four call sites — where the
    truth is "the read failed". `_worktree_root` already fails closed for the same command.
    """
    wt = dirty_lane(hub, "wt-inside-read")
    out = _run_tool_with_shim(git_shim, "LW_FAIL_INSIDE_TREE", "pin", cwd=wt)
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "is not a git work tree" not in out.stderr, out.stderr
    assert "could not decide" in out.stderr, out.stderr


def test_a_failed_show_ref_read_is_not_a_missing_branch(hub: Path, git_shim: Path) -> None:
    """A FAILED `show-ref` read printed "branch <b> no longer exists; recreating it".

    The branch existed; git then refused `-b` and the recovery failed with a message about
    the branch already existing — a false fact followed by an impossible instruction.
    """
    wt = dirty_lane(hub, "wt-showref")
    branch = "lane-wt-showref"
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    out = _run_tool_with_shim(
        git_shim, "LW_FAIL_SHOW_REF", "recover", ref, "--into", str(wt), "--branch", branch, cwd=hub
    )
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "no longer exists" not in out.stderr, out.stderr
    assert "could not read" in out.stderr, out.stderr


def test_a_failed_ls_files_stage_read_does_not_silence_the_ita_warning(
    hub: Path, git_shim: Path
) -> None:
    """A FAILED `ls-files --stage` deleted the intent-to-add warning, claiming a full pin.

    `_out_z(..., check=False)` returned `[]`, `_intent_to_add` returned `[]`, and `pin`
    printed `pinned N file(s)` with an EMPTY stderr — over a pin that omits an `add -N`
    path, so `recover` turns ` A ita.py` into `?? ita.py` and the lane's `commit -a` silently
    drops it.
    """
    wt = clean_lane(hub, "wt-ita-read")
    (wt / "ita.py").write_text("intent to add\n")
    assert git("add", "-N", "ita.py", cwd=wt).returncode == 0

    out = _run_tool_with_shim(git_shim, "LW_FAIL_LS_FILES_STAGE", "pin", cwd=wt)
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "could not read the index" in out.stderr, out.stderr
    assert "pinned " not in out.stdout, out.stdout


def test_a_failed_ls_tree_read_does_not_silence_the_gitlink_warning(
    hub: Path, git_shim: Path
) -> None:
    """A FAILED gitlink probe deleted the "nested repository" warning — same pin, opposite verdict."""
    wt = clean_lane(hub, "wt-gitlink-read")
    nested = wt / "nested"
    nested.mkdir()
    assert git("init", "-q", cwd=nested).returncode == 0
    # ⛔ A nested repo does NOT inherit the hub's identity, and a CI/cell clone has no
    # global `user.email` — without this the `commit` below exits 128 ("Author identity
    # unknown"), which reads as the pin's failure. Same convention as the other nested
    # repos in this file (`git config user.email`, not an ambient identity).
    git("config", "user.email", "t@example.com", cwd=nested)
    git("config", "user.name", "t", cwd=nested)
    (nested / "x.py").write_text("nested content\n")
    assert git("add", "x.py", cwd=nested).returncode == 0
    assert git("commit", "-qm", "n", cwd=nested).returncode == 0

    out = _run_tool_with_shim(git_shim, "LW_FAIL_LS_TREE", "pin", cwd=wt)
    assert out.returncode == 3, f"{out.returncode}: {out.stdout}{out.stderr}"
    # The probe is one of several `ls-tree` reads in `pin`; whichever fails, the pin must
    # not be announced — a failed read may never silently delete the gitlink warning.
    assert "pinned " not in out.stdout, out.stdout
    assert "could not read the pin's tree" in out.stderr, out.stderr


def test_pin_refuses_a_directory_INSIDE_a_worktree(hub: Path) -> None:
    """`pin --path <hub>/.worktrees/<lane>` must not snapshot the HUB under the LANE's key.

    `_lane_root` keeps a gitignored/empty directory as its own lane KEY (so `list` finds the
    lane's pins), and `--is-inside-work-tree` is TRUE for it because it sits inside the HUB —
    so `pin` snapshotted the hub's working state and labelled it with the lane's key, or said
    "clean — nothing to pin" over a hub with nothing to save. Both are the false "your work is
    safe" verdict, in the exact state the RECIPE describes.
    """
    with (hub / ".gitignore").open("a") as fh:
        fh.write(".worktrees/\n")
    assert git("add", ".gitignore", cwd=hub).returncode == 0
    assert git("commit", "-qm", "ignore lanes", cwd=hub).returncode == 0
    lane = hub / ".worktrees" / "lane-x"
    lane.mkdir(parents=True)
    (hub / "hub_new.py").write_text("the HUB's work\n")
    try:
        out = run_tool("pin", "--path", str(lane), cwd=hub)
        assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
        assert "is not a worktree root" in out.stderr, out.stderr
        assert "clean — nothing to pin" not in out.stdout, out.stdout
        assert "pinned " not in out.stdout, out.stdout
        refs = git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub)
        assert refs.stdout.strip() == "", f"a pin was minted over the hub: {refs.stdout}"
    finally:
        (hub / "hub_new.py").unlink(missing_ok=True)


def test_pin_from_an_IGNORED_SUBDIRECTORY_of_a_LANE_keys_the_LANE(hub: Path) -> None:
    """`cd <lane>/build && pin` must pin THE LANE — not refuse, and never key `build`.

    `_lane_root` answers an ignored or empty path as its OWN lane key, which is right for the
    RECREATED-lane shape (`<hub>/.worktrees/<lane>`, inside the HUB) and wrong for every
    ignored subdirectory of a LIVE lane (`build/`, `.venv`). `pin` then refused with "… is not
    a worktree root — it is a directory inside <lane>", telling an operator standing inside
    their lane to run inside their lane — and on `origin/main` the same invocation pinned the
    lane correctly (`Path(_worktree_root(root) or root)`), so this was a regression of the
    tool's PRIMARY protection verb, in the state a lane is most likely to be in.

    `list` was already given this discriminator (`test_list_from_an_IGNORED_SUBDIRECTORY_finds_
    the_lanes_pins`); the two verbs must not disagree about one state.
    """
    wt = dirty_lane(hub, "wt-ignoredpin")
    with (wt / ".gitignore").open("a") as fh:
        fh.write("build/\n")
    assert git("add", ".gitignore", cwd=wt).returncode == 0
    assert git("commit", "-qm", "ignore build", cwd=wt).returncode == 0
    (wt / "build").mkdir()
    (wt / "build" / "out.bin").write_text("artifact\n")

    pinned = run_tool("pin", cwd=wt / "build")
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    # The pin was taken AT the lane: keying `build` would have said "from …/build" and minted
    # a key `list` at the lane root does not read.
    assert f"from {wt.resolve()}" in pinned.stdout, pinned.stdout
    ref = pin_ref(pinned.stdout)
    at_root = run_tool("list", cwd=wt)
    assert ref in at_root.stdout, at_root.stdout
    in_build = run_tool("list", cwd=wt / "build")
    assert ref in in_build.stdout, in_build.stdout
    assert "has no wip pin" not in in_build.stdout, in_build.stdout


def test_pin_from_an_EMPTY_SUBDIRECTORY_of_a_LANE_keys_the_LANE(hub: Path) -> None:
    """`cd <lane>/newpkg && pin` (a JUST-created, non-ignored directory) must pin the lane.

    Same regression as the ignored-subdir arm: `_lane_root` reads ANY empty directory as its
    own lane key, so `pin` refused a lane that was standing in the very directory it had just
    created — the state a lane is in while it starts a new package. On `origin/main` it pinned
    the enclosing worktree.
    """
    wt = dirty_lane(hub, "wt-emptypin")
    sub = wt / "newpkg"
    sub.mkdir()

    pinned = run_tool("pin", cwd=sub)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    assert f"from {wt.resolve()}" in pinned.stdout, pinned.stdout
    listed = run_tool("list", cwd=wt)
    assert pin_ref(pinned.stdout) in listed.stdout, listed.stdout


def test_pin_refuses_a_POPULATED_recreated_lane_path(hub: Path) -> None:
    """Content in `<hub>/.worktrees/<lane>` does not make it a lane — the HUB is still behind it.

    The refusal must key on WHERE the directory sits (inside the repository's MAIN worktree),
    not on "ignored AND empty": a recreated lane path that already holds files is the shape
    most likely to be rescued into, and keying the hub for it would snapshot the hub's work
    under the lane's key — the false "your work is safe" verdict the refusal exists for.
    """
    with (hub / ".gitignore").open("a") as fh:
        fh.write(".worktrees/\n")
    assert git("add", ".gitignore", cwd=hub).returncode == 0
    assert git("commit", "-qm", "ignore lanes", cwd=hub).returncode == 0
    lane = hub / ".worktrees" / "lane-y"
    lane.mkdir(parents=True)
    (lane / "rescued.py").write_text("copied out of a dying lane\n")
    (hub / "hub_new.py").write_text("the HUB's work\n")
    try:
        out = run_tool("pin", "--path", str(lane), cwd=hub)
        assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
        assert "is not a worktree root" in out.stderr, out.stderr
        assert "pinned " not in out.stdout, out.stdout
        refs = git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub)
        assert refs.stdout.strip() == "", f"a pin was minted over the hub: {refs.stdout}"
    finally:
        (hub / "hub_new.py").unlink(missing_ok=True)


def test_pin_from_an_IGNORED_SUBDIRECTORY_of_the_HUB_names_the_HUB(hub: Path) -> None:
    """A HUB subdirectory must be told to pin the HUB — never "run inside the lane".

    The refusal is CORRECT: `_lane_root` keeps an ignored/empty directory's own key inside the
    MAIN worktree, so `cd <hub>/build && pin` would snapshot the HUB under a key of its own.
    But that shape lives in the HUB as well as in the RECIPE's recreated lane — `<hub>/build`,
    `<hub>/.venv`, a just-created `<hub>/newpkg` — and there the old message named a lane that
    does not exist and told the operator to go stand in it. `origin/main` PINNED the hub from
    `<hub>/build` (rc 0), so this message is the only route back to a pin the operator can
    actually take; it must name the worktree root.

    Falsification: revert the `_is_main_worktree` branch in the refusal and the second
    assertion below fails on "run inside the lane" while the first still passes — the exit
    code and "not a worktree root" are the SAME in both shapes, so only the remedy text
    discriminates, which is exactly why it needs a pin.
    """
    with (hub / ".gitignore").open("a") as fh:
        fh.write("build/\n")
    assert git("add", ".gitignore", cwd=hub).returncode == 0
    assert git("commit", "-qm", "ignore build", cwd=hub).returncode == 0
    build = hub / "build"
    build.mkdir()
    (build / "artifact.bin").write_text("ignored, but the HUB is what is behind it\n")
    (hub / "hub_new.py").write_text("the HUB's work\n")
    try:
        out = run_tool("pin", cwd=build)
        assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
        assert "is not a worktree root" in out.stderr, out.stderr
        assert str(hub) in out.stderr, (
            "the refusal must name the worktree root the caller CAN pin — the hub — "
            f"because no lane exists at this path: {out.stderr}"
        )
        assert "run inside the lane" not in out.stderr, (
            "a HUB subdirectory was told to `run inside the lane`; there is no lane here, "
            f"so the remedy names a place that does not exist: {out.stderr}"
        )
        refs = git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub)
        assert refs.stdout.strip() == "", f"a pin was minted over the hub: {refs.stdout}"
    finally:
        (hub / "hub_new.py").unlink(missing_ok=True)


def test_one_unreadable_index_only_path_does_not_cost_the_whole_pin(hub: Path) -> None:
    """The `add -f` step for index-only paths must degrade, not fail, on one bad path.

    `add -A` already survives that (`--ignore-errors` + a warning); the index-only step
    re-introduced the failure: measured, one mode-000 force-added `.env.local` made `pin`
    exit 3 with NO ref written over a lane whose real work was perfectly readable.
    """
    wt = clean_lane(hub, "wt-badindexonly")
    with (wt / ".gitignore").open("a") as fh:
        fh.write("local/\n")
    assert git("add", ".gitignore", cwd=wt).returncode == 0
    assert git("commit", "-qm", "ignore", cwd=wt).returncode == 0
    (wt / "local").mkdir()
    secret = wt / "local" / "secret.conf"
    secret.write_text("secret\n")
    assert git("add", "-f", "local/secret.conf", cwd=wt).returncode == 0
    (wt / "tracked.txt").write_text("the lane's real work\n")
    secret.chmod(0o000)
    try:
        out = run_tool("pin", cwd=wt)
        assert out.returncode == 0, f"{out.returncode}: {out.stdout}{out.stderr}"
        assert "pinned " in out.stdout, out.stdout
        assert "could NOT be added" in out.stderr, out.stderr
        refs = git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub)
        assert refs.stdout.strip() != "", "no pin was written"
    finally:
        with contextlib.suppress(FileNotFoundError):
            secret.chmod(0o600)


def test_a_failed_index_listing_is_announced_not_silently_ignored(
    hub: Path, git_shim: Path
) -> None:
    """A FAILED bare `ls-files -z` must not render as "there are no index-only paths".

    Silent, the pin omitted a force-added ignored path while printing `pinned N file(s)` with
    an EMPTY stderr — so `recover` later claimed the whole state over work absent from disk.
    """
    wt = clean_lane(hub, "wt-lsfiles-plain")
    with (wt / ".gitignore").open("a") as fh:
        fh.write("local/\n")
    assert git("add", ".gitignore", cwd=wt).returncode == 0
    assert git("commit", "-qm", "ignore", cwd=wt).returncode == 0
    (wt / "local").mkdir()
    (wt / "local" / "secret.conf").write_text("secret\n")
    assert git("add", "-f", "local/secret.conf", cwd=wt).returncode == 0

    out = _run_tool_with_shim(git_shim, "LW_FAIL_LSFILES_PLAIN", "pin", cwd=wt)
    assert out.returncode == 0, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "NOT verified in this pin" in out.stderr or "could not be listed" in out.stderr, (
        out.stderr
    )


def test_list_without_a_path_uses_the_CALLERS_lane_key(hub: Path) -> None:
    """`list` run inside a recreated lane dir must not key on the HUB.

    Round 29 fixed the `--path` form only; the DEFAULT invocation — what a lane runs with its
    cwd inside its own directory — resolved `_repo_root()` to the hub and printed "lane
    hub-<key> has no wip pin" while the lane's pins sat one ref away.
    """
    with (hub / ".gitignore").open("a") as fh:
        fh.write(".worktrees/\n")
    assert git("add", ".gitignore", cwd=hub).returncode == 0
    assert git("commit", "-qm", "ignore lanes", cwd=hub).returncode == 0
    (hub / ".worktrees").mkdir(exist_ok=True)
    lane = hub / ".worktrees" / "lane-listed"
    made = git("worktree", "add", "-q", str(lane), "-b", "lane-listed", cwd=hub)
    assert made.returncode == 0, made.stderr
    (lane / "tracked.txt").write_text("the lane's work\n")
    pinned = run_tool("pin", cwd=lane)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, lane).returncode == 0
    lane.mkdir()
    try:
        out = run_tool("list", cwd=lane)  # NO --path
        assert out.returncode == 0, out.stderr
        assert ref in out.stdout, f"list from the lane dir missed its own pin: {out.stdout}"
        assert "has no wip pin" not in out.stdout, out.stdout
    finally:
        with contextlib.suppress(OSError):
            lane.rmdir()


def test_list_reports_a_real_file_count_from_the_lane_directory(hub: Path) -> None:
    """`ls-tree` without `--full-tree` is limited to the cwd, so every pin reported 0 files.

    `ls-tree` (unlike `ls-files`) uses the cwd as an implicit pathspec prefix, so
    `list --path <hub>/.worktrees/<lane>` said `0 file(s)` for a pin holding four — "this pin
    holds nothing" is the one conclusion `_describe` forbids rendering wrongly.
    """
    with (hub / ".gitignore").open("a") as fh:
        fh.write(".worktrees/\n")
    assert git("add", ".gitignore", cwd=hub).returncode == 0
    assert git("commit", "-qm", "ignore lanes", cwd=hub).returncode == 0
    (hub / ".worktrees").mkdir(exist_ok=True)
    lane = hub / ".worktrees" / "lane-counted"
    made = git("worktree", "add", "-q", str(lane), "-b", "lane-counted", cwd=hub)
    assert made.returncode == 0, made.stderr
    (lane / "tracked.txt").write_text("the lane's work\n")
    (lane / "extra.py").write_text("more work\n")
    pinned = run_tool("pin", cwd=lane)
    assert pinned.returncode == 0, pinned.stderr
    expected = int(pinned.stdout.split("pinned ")[1].split(" file")[0])
    assert expected > 0, pinned.stdout

    # ⛔ FROM THE RECREATED (non-worktree-root) PATH: that is the only cwd where `ls-tree`
    # applies an implicit pathspec prefix, so it is the only place `--full-tree` matters.
    assert destroy(hub, lane).returncode == 0
    lane.mkdir()
    out = run_tool("list", "--path", str(lane), cwd=hub)
    assert out.returncode == 0, out.stderr
    assert f"{expected} file(s)" in out.stdout, out.stdout
    assert " 0 file(s)" not in out.stdout, out.stdout


def test_recover_path_pointed_at_a_SUBDIRECTORY_does_not_falsely_fail(hub: Path) -> None:
    """`--path <lane>/src` must not compare the restore against paths that never existed.

    `ls-tree` uses the cwd as an implicit pathspec prefix AND rebases what it prints
    (measured: from `src/`, `ls-tree -r --name-only <sha>` returns `a.txt`, not
    `src/a.txt`), so `missing` named every pin file, and a byte-complete restore was
    reported as `restored 0 of 1 file(s)` at exit 3 — with a "sparse checkout" cause and
    two remedies (one of them `--force`, the flag that can revert committed work) that
    cannot help. `pin` refuses a non-root `--path`; `recover` accepted one silently.
    """
    wt = dirty_lane(hub, "wt-subdirpath")
    (wt / "src").mkdir(exist_ok=True)
    (wt / "src" / "one.py").write_text("the lane's work\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)
    expected = int(pinned.stdout.split("pinned ")[1].split(" file")[0])

    # ⛔ `--path` names a LIVE subdirectory ON PURPOSE. A DEAD `--path` falls back to
    # `_repo_root()` (so the bug hides), while a live subdirectory is the cwd `ls-tree` runs
    # in — and `ls-tree` then applies it as a pathspec prefix and rebases the names it
    # prints (measured: from `src/`, `a.txt` instead of `src/a.txt`).
    target = hub.parent / "wt-subdirpath-recovery"
    out = run_tool("recover", ref, "--into", str(target), "--path", str(wt / "src"), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert f"recovered {expected} file(s)" in out.stdout, out.stdout
    assert "did NOT appear" not in out.stderr, out.stderr
    assert (target / "src" / "one.py").read_text() == "the lane's work\n"


def test_list_from_an_IGNORED_SUBDIRECTORY_finds_the_lanes_pins(hub: Path) -> None:
    """An ignored subdir (`build/`, `.venv`) of a LIVE lane is not its own lane.

    `_lane_root` answers an ignored path as its own key — right for the RECREATED-lane
    shape — so `list` run from `<lane>/build` said `lane build-… has no wip pin` at exit 0
    over a lane that was fully pinned, and RECIPE step 3 reads an empty pin list as "the
    work is genuinely gone". Both keys are read now.
    """
    wt = dirty_lane(hub, "wt-ignoredsub")
    with (wt / ".gitignore").open("a") as fh:
        fh.write("build/\n")
    assert git("add", ".gitignore", cwd=wt).returncode == 0
    assert git("commit", "-qm", "ignore build", cwd=wt).returncode == 0
    (wt / "build").mkdir()
    (wt / "build" / "out.bin").write_text("artifact\n")
    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stderr
    ref = pin_ref(pinned.stdout)

    from_build = run_tool("list", cwd=wt / "build")
    assert from_build.returncode == 0, from_build.stderr
    assert ref in from_build.stdout, f"list from build/ missed the lane's pin: {from_build.stdout}"
    assert "has no wip pin" not in from_build.stdout, from_build.stdout


def test_a_DELETED_CWD_is_a_state_error_not_a_traceback(hub: Path, tmp_path: Path) -> None:
    """#10812 itself: the process sits in a directory removed under it.

    `Path.cwd()` raises `FileNotFoundError` there, so `pin` — the verb that exists to
    protect that lane — died with a traceback at exit 1, the code this file reserves as
    "never a verdict about a lane's work", and no `recipe` pointer ever reached the caller.
    """
    prog = tmp_path / "deadcwd.py"
    prog.write_text(
        "import os, subprocess, sys\n"
        "d = sys.argv[1]\n"
        "os.makedirs(d, exist_ok=True)\n"
        "os.chdir(d)\n"
        "os.rmdir(d)\n"
        "cp = subprocess.run([sys.argv[2], sys.argv[3], 'pin'], capture_output=True, text=True)\n"
        "sys.stdout.write(cp.stdout)\n"
        "sys.stderr.write(cp.stderr)\n"
        "sys.exit(cp.returncode)\n"
    )
    dead = tmp_path / "dead-lane-dir"
    out = subprocess.run(
        [PYTHON, str(prog), str(dead), PYTHON, str(TOOL)],
        cwd=str(hub),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8:strict"},
    )
    assert out.returncode == 2, f"{out.returncode}: {out.stdout}{out.stderr}"
    assert "Traceback" not in out.stderr, out.stderr
    assert "no longer exists" in out.stderr, out.stderr
    assert "list --all" in out.stderr, out.stderr


def _sparse_lane(hub: Path, name: str) -> Path:
    """A lane whose worktree is a sparse cone: `keep/` in, everything else out."""
    (hub / "keep").mkdir(exist_ok=True)
    (hub / "keep" / "a.txt").write_text("keep v1\n")
    (hub / "prune").mkdir(exist_ok=True)
    (hub / "prune" / "b.txt").write_text("prune v1\n")
    assert git("add", "-A", cwd=hub).returncode == 0
    assert git("commit", "-qm", "keep + prune", cwd=hub).returncode == 0
    wt = hub.parent / name
    made = git("worktree", "add", "-q", str(wt), "-b", f"lane-{name}", cwd=hub)
    assert made.returncode == 0, made.stderr
    assert git("sparse-checkout", "set", "--cone", "keep", cwd=wt).returncode == 0, (
        "this git cannot make a sparse cone; the test needs it"
    )
    return wt


def test_a_sparse_lane_pins_work_OUTSIDE_its_cone(hub: Path) -> None:
    """`git add -A` is sparse-aware: it skips out-of-cone paths at rc 0 with EMPTY stderr.

    Measured: a sparse lane whose only change was a tracked file outside the cone made
    `read-tree HEAD` + `add -A` + `write-tree` produce a tree EQUAL to HEAD's while
    `git status` in the same directory said ` M prune/b.txt` — so `pin` printed
    "clean — nothing to pin", wrote NO ref, and the lane's only copy of those bytes stayed
    a file on disk with no object in the DB. That is #10812's loss under a false
    "nothing to save" verdict. `--sparse` captures it; the `git status` cross-check is the
    second line of defence and refuses the CLEAN verdict outright.
    """
    wt = _sparse_lane(hub, "wt-sparse-only")
    (wt / "prune").mkdir(exist_ok=True)
    (wt / "prune" / "b.txt").write_text("THE LANE'S ONLY COPY\n")
    assert " M prune/b.txt" in git("status", "--porcelain", cwd=wt).stdout

    out = run_tool("pin", cwd=wt)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "clean — nothing to pin" not in out.stdout, out.stdout
    ref = pin_ref(out.stdout)
    body = git("show", f"{ref}:prune/b.txt", cwd=hub)
    assert body.stdout == "THE LANE'S ONLY COPY\n", (
        f"the pin held {body.stdout!r} — the lane's out-of-cone work is not in it"
    )


def test_a_sparse_lane_pins_out_of_cone_work_BESIDE_in_cone_work(hub: Path) -> None:
    """The mixed case: an in-cone edit used to produce `pinned N file(s)` over HEAD's bytes."""
    wt = _sparse_lane(hub, "wt-sparse-mixed")
    (wt / "keep" / "a.txt").write_text("keep v2 — the lane's work\n")
    (wt / "prune").mkdir(exist_ok=True)
    (wt / "prune" / "b.txt").write_text("prune v2 — also the lane's work\n")

    out = run_tool("pin", cwd=wt)
    assert out.returncode == 0, out.stdout + out.stderr
    ref = pin_ref(out.stdout)
    body = git("show", f"{ref}:prune/b.txt", cwd=hub)
    assert body.stdout == "prune v2 — also the lane's work\n", (
        f"the pin kept HEAD's content for the out-of-cone path: {body.stdout!r}"
    )
    assert git("show", f"{ref}:keep/a.txt", cwd=hub).stdout.startswith("keep v2"), "in-cone lost"


def test_an_old_git_without_add_sparse_still_refuses_the_false_clean(
    hub: Path, git_shim: Path
) -> None:
    """`--sparse` arrived in git 2.41; before it, the skip is silent — so a THIRD view decides.

    Measured on a shim that rejects `--sparse` (an old git): `read-tree HEAD` + `add -A` +
    `write-tree` returns HEAD's tree while `git status` reports ` M prune/b.txt`, so without
    the cross-check the pin would print "clean — nothing to pin" over the lane's only copy.
    The pin must REFUSE loudly instead, at a code that is not "nothing to save".
    """
    wt = _sparse_lane(hub, "wt-sparse-oldgit")
    (wt / "prune").mkdir(exist_ok=True)
    (wt / "prune" / "b.txt").write_text("THE LANE'S ONLY COPY\n")

    out = _run_tool_with_shim(git_shim, "LW_OLD_GIT_ADD", "pin", cwd=wt)
    assert out.returncode != 0, f"a silent skip must not read as success: {out.stdout}"
    assert "clean — nothing to pin" not in out.stdout, out.stdout
    assert "does NOT hold" in out.stderr, out.stderr
    assert "prune/b.txt" in out.stderr, out.stderr
    refs = git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub)
    assert refs.stdout.strip() == "", f"a pin claiming completeness was minted: {refs.stdout}"


def test_a_failed_status_read_cannot_produce_the_clean_verdict(hub: Path, git_shim: Path) -> None:
    """`[]` from a FAILED `git status` is the positive answer "nothing is dirty".

    The cross-check is the only defence against a silent sparse skip on a git without
    `add --sparse`. Measured with a shim failing ONLY `status`, over a sparse lane's sole
    edit: exit 0, "clean — nothing to pin", no ref, and the lane's bytes in NO object.
    """
    wt = _sparse_lane(hub, "wt-sparse-nostatus")
    (wt / "prune").mkdir(exist_ok=True)
    (wt / "prune" / "b.txt").write_text("THE LANE'S ONLY COPY\n")

    # ⛔ BOTH: an old git (so the skip is SILENT) and the failing read. With `--sparse`
    # working, the pin captures the path and the cross-check never runs — the finding needs
    # the <2.41 path the tool deliberately supports.
    out = _run_tool_with_shim(git_shim, "LW_FAIL_STATUS", "pin", cwd=wt, also=("LW_OLD_GIT_ADD",))
    assert out.returncode != 0, f"a failed read must not read as success: {out.stdout}"
    assert "clean — nothing to pin" not in out.stdout, out.stdout
    assert "could not read the state" in out.stderr, out.stderr
    refs = git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub)
    assert refs.stdout.strip() == "", f"a pin claiming completeness was minted: {refs.stdout}"


def test_a_failed_content_read_cannot_produce_the_clean_verdict(hub: Path, git_shim: Path) -> None:
    """One unreadable `hash-object` must not answer "the pin holds this path"."""
    wt = _sparse_lane(hub, "wt-sparse-nohash")
    (wt / "prune").mkdir(exist_ok=True)
    (wt / "prune" / "b.txt").write_text("THE LANE'S ONLY COPY\n")

    out = _run_tool_with_shim(
        git_shim, "LW_FAIL_HASH_OBJECT", "pin", cwd=wt, also=("LW_OLD_GIT_ADD",)
    )
    assert out.returncode != 0, f"a failed read must not read as success: {out.stdout}"
    assert "clean — nothing to pin" not in out.stdout, out.stdout
    assert "prune/b.txt" in out.stderr, out.stderr
    refs = git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub)
    assert refs.stdout.strip() == "", f"a pin claiming completeness was minted: {refs.stdout}"


def test_recover_WARNS_that_a_nested_repo_came_back_empty(hub: Path) -> None:
    """`pin` warned about the gitlink; `recover` said an unqualified "recovered N file(s)".

    Measured: a lane holding `lane.py` plus a nested repo `vendored/` (one commit, one
    uncommitted change) recovered to `recovered 3 file(s)` with EMPTY stderr, and
    `vendored/` was an EMPTY DIRECTORY — its commits and its uncommitted work gone. The pin
    knew; the recovery message did not say so.
    """
    wt = hub.parent / "wt-gitlink-recover"
    made = git("worktree", "add", "-q", str(wt), "-b", "lane-gitlink-recover", cwd=hub)
    assert made.returncode == 0, made.stderr
    vendored = wt / "vendored"
    vendored.mkdir()
    assert git("init", "-q", cwd=vendored).returncode == 0
    # A nested repo does NOT inherit the hub's identity; a CI/cell clone has no global
    # `user.email`, so without this the `commit` exits 128 and the test fails for a reason
    # that has nothing to do with the pin.
    git("config", "user.email", "n@example.com", cwd=vendored)
    git("config", "user.name", "n", cwd=vendored)
    (vendored / "inner.py").write_text("inner v1\n")
    assert git("add", "inner.py", cwd=vendored).returncode == 0
    assert git("commit", "-qm", "inner", cwd=vendored).returncode == 0
    (vendored / "inner.py").write_text("inner v2 uncommitted\n")
    (wt / "lane.py").write_text("the lane's work\n")

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    assert "nested repo" in pinned.stderr, pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    target = hub.parent / "wt-gitlink-recover-2"
    out = run_tool("recover", ref, "--path", str(hub), "--into", str(target), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "nested repo" in out.stderr, (
        f"recover claimed an unqualified success over an empty nested repo: {out.stderr!r}"
    )


def test_a_sparse_lane_with_IN_CONE_WORK_too_still_refuses_the_partial_pin(
    hub: Path, git_shim: Path
) -> None:
    """The cross-check must not be gated on the pin already LOOKING clean.

    Measured on an old git (no `add --sparse`): a sparse lane with an in-cone edit AND its
    only copy of an out-of-cone file pinned HEAD's bytes for that path with an EMPTY stderr,
    printed `pinned 2 file(s)`, exited 0 — and `recover` restored HEAD over the work. The
    check exists for exactly the case where `tree != head_tree`.
    """
    wt = _sparse_lane(hub, "wt-sparse-mixed-oldgit")
    (wt / "keep" / "a.txt").write_text("keep v2 — the lane's work\n")
    (wt / "prune").mkdir(exist_ok=True)
    (wt / "prune" / "b.txt").write_text("THE LANE'S ONLY COPY\n")

    out = _run_tool_with_shim(git_shim, "LW_OLD_GIT_ADD", "pin", cwd=wt, also=("LW_OLD_GIT_ADD",))
    # ⛔ TWO ACCEPTED OUTCOMES, EACH CHECKED FOR THE THING THAT MATTERS. `rc != 0` alone was not
    # discriminating: when the shim's `--sparse` rejection does not fire, the pin is COMPLETE and
    # exits 0 with an empty stderr, so the test went red for a reason unrelated to the tool and
    # could not tell the two apart (measured, one run in thirteen). Assert the ARTIFACT.
    if out.returncode == 0:
        ref = pin_ref(out.stdout)
        body = git("show", f"{ref}:prune/b.txt", cwd=hub).stdout
        assert body == "THE LANE'S ONLY COPY\n", (
            f"the pin kept HEAD's bytes for the out-of-cone path: {body!r}"
        )
        return
    assert "does NOT hold" in out.stderr, out.stderr
    assert "prune/b.txt" in out.stderr, out.stderr
    refs = git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub)
    assert refs.stdout.strip() == "", f"a pin claiming completeness was minted: {refs.stdout}"


def test_a_failed_force_add_is_a_refusal_not_a_capture(hub: Path, git_shim: Path) -> None:
    """`git add -f`'s rc was read NOWHERE, so a missing `error:` line made it "captured".

    Measured: an `AM` force-added ignored path whose v2 edit could not be indexed (a
    marker-free `fatal: unable to write new index file`) pinned v1 only, printed
    `pinned 2 file(s)` with an EMPTY stderr, and `recover` restored the file ABSENT.
    """
    wt = clean_lane(hub, "wt-forceadd-fail")
    with (wt / ".gitignore").open("a") as fh:
        fh.write("local/\n")
    assert git("add", ".gitignore", cwd=wt).returncode == 0
    assert git("commit", "-qm", "ignore", cwd=wt).returncode == 0
    (wt / "local").mkdir()
    secret = wt / "local" / "secret.conf"
    secret.write_text("STAGED v1\n")
    assert git("add", "-f", "local/secret.conf", cwd=wt).returncode == 0
    secret.write_text("THE LANE'S ONLY COPY v2\n")

    out = _run_tool_with_shim(git_shim, "LW_FAIL_FORCE_ADD", "pin", cwd=wt)
    assert out.returncode != 0, f"a failed write must not read as success: {out.stdout}"
    assert "index-only" in out.stderr, out.stderr
    refs = git("for-each-ref", "--format=%(refname)", "refs/pi-lane-wip/", cwd=hub)
    assert refs.stdout.strip() == "", f"a partial pin was minted: {refs.stdout}"


def test_recover_from_a_SUBDIRECTORY_path_still_warns_about_a_nested_repo(hub: Path) -> None:
    """`_warn_about_gitlinks` needs `--full-tree`, or a subdirectory `--path` silences it.

    Measured with the same ref and repo: `--path <hub>` warned ("1 nested repository/ies …
    vendored") while `--path <hub>/src` printed an unqualified `recovered N file(s)` and
    `vendored/` came back EMPTY.
    """
    wt = hub.parent / "wt-gitlink-subdir"
    made = git("worktree", "add", "-q", str(wt), "-b", "lane-gitlink-subdir", cwd=hub)
    assert made.returncode == 0, made.stderr
    vendored = wt / "vendored"
    vendored.mkdir()
    assert git("init", "-q", cwd=vendored).returncode == 0
    # A nested repo does NOT inherit the hub's identity; a CI/cell clone has no global
    # `user.email`, so without this the `commit` exits 128 and the test fails for a reason
    # that has nothing to do with `--full-tree`.
    git("config", "user.email", "n@example.com", cwd=vendored)
    git("config", "user.name", "n", cwd=vendored)
    (vendored / "inner.py").write_text("inner v1\n")
    assert git("add", "inner.py", cwd=vendored).returncode == 0
    assert git("commit", "-qm", "inner", cwd=vendored).returncode == 0
    (wt / "src").mkdir(exist_ok=True)
    (wt / "src" / "x.py").write_text("the lane's work\n")

    pinned = run_tool("pin", cwd=wt)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    ref = pin_ref(pinned.stdout)
    assert destroy(hub, wt).returncode == 0

    # ⛔ THE `--path` MUST BE A SUBDIRECTORY THAT REALLY EXISTS IN THE HUB. `wt/src` vanishes
    # with the worktree, and a MISSING `--path` falls back to `_repo_root()` — the one spelling
    # where `--full-tree` is irrelevant, so the test passed for the wrong reason (measured:
    # mutating `--full-tree` away kept it green).
    (hub / "docs").mkdir(exist_ok=True)
    (hub / "docs" / "two.md").write_text("hub docs\n")
    target = hub.parent / "wt-gitlink-subdir-2"
    out = run_tool("recover", ref, "--path", str(hub / "docs"), "--into", str(target), cwd=hub)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "nested repo" in out.stderr, (
        f"a subdirectory --path silenced the gitlink warning: {out.stderr!r}"
    )


def test_ONE_unreadable_path_does_not_disable_the_completeness_check(
    hub: Path, git_shim: Path
) -> None:
    """A single `chmod 000` artifact must not switch the sparse cross-check off.

    Measured on an old git: a sparse lane carrying one unreadable file printed
    `pinned 3 file(s)` with a warning naming ONLY that file, while the pin held HEAD's bytes
    for the out-of-cone path — `unreadable` disabled the one view that would have caught it,
    and `recover` later reported success over HEAD's bytes.
    """
    wt = _sparse_lane(hub, "wt-sparse-unreadable")
    # ⛔ AN IN-CONE EDIT TOO, or the pin is `worktree_clean` and the older
    # `unreadable and worktree_clean` refusal fires first — masking the gate this test is about.
    (wt / "keep" / "a.txt").write_text("keep v2 — the lane's work\n")
    (wt / "prune").mkdir(exist_ok=True)
    (wt / "prune" / "b.txt").write_text("THE LANE'S ONLY COPY\n")
    blocked = wt / "root_owned.bin"
    blocked.write_text("an artifact the lane does not care about\n")
    blocked.chmod(0o000)
    try:
        if os.access(blocked, os.R_OK):
            # ⛔ THE PREMISE IS UNMAKEABLE AS ROOT: a mode-000 file is readable, `git add`
            # indexes it, `unreadable` stays empty, so the OLD gate is active too and this
            # test would take the `rc != 0` branch and PASS with the fix reverted. Skip
            # rather than report a green that proves nothing.
            pytest.skip(
                "mode-000 is readable to this user (root), so the unreadable-path premise "
                "cannot be established and reverting the gate would not fail this test"
            )
        out = _run_tool_with_shim(
            git_shim, "LW_OLD_GIT_ADD", "pin", cwd=wt, also=("LW_OLD_GIT_ADD",)
        )
        if out.returncode == 0:
            ref = pin_ref(out.stdout)
            body = git("show", f"{ref}:prune/b.txt", cwd=hub).stdout
            assert body == "THE LANE'S ONLY COPY\n", (
                f"the pin kept HEAD's bytes while warning only about root_owned.bin: {body!r}"
            )
            return
        assert "does NOT hold" in out.stderr, out.stderr
        assert "prune/b.txt" in out.stderr, out.stderr
    finally:
        blocked.chmod(0o600)


def test_a_fifo_does_not_hang_the_pin(hub: Path) -> None:
    """`open()` on a FIFO with no writer BLOCKS FOREVER, so `pin` must probe with `stat`.

    `is_dir()`/`is_symlink()` are checked first, so a FIFO reached the readability probe and
    `open()` never returned: measured, `pin` produced NO output and wrote NO ref until it was
    killed, while the pre-fix head pinned the lane's real work and warned about the FIFO. A
    hung `pin` is worse than a failed one — a caller with a timeout proceeds UNPINNED, so the
    readable work is destroyed by the very path this tool exists to survive.
    """
    wt = clean_lane(hub, "wt-fifo")
    (wt / "o.txt").write_text("the lane's real work\n")
    # ⛔ THE FIFO MUST SIT AT A *TRACKED* PATH. An untracked FIFO never enters
    # `git status --porcelain` at all (measured), so it would never reach the readability
    # probe and the test would pass with the hang present. Replacing a tracked file with a
    # FIFO reports ` M tracked.txt`, which is the shape that reached `open()`.
    os.unlink(wt / "tracked.txt")
    os.mkfifo(wt / "tracked.txt")

    pinned = run_tool("pin", cwd=wt, timeout=60)
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    ref = pin_ref(pinned.stdout)
    # The FIFO itself has no blob to pin, but the REAL work must still be captured.
    assert git("show", f"{ref}:o.txt", cwd=hub).stdout == "the lane's real work\n"


def test_a_permission_denied_path_is_announced_by_name(hub: Path) -> None:
    """`git add` reports a skipped path BARE (`d/f.txt: Permission denied`) and exits 0.

    No `error:`/`warning:` prefix, so `_SKIP_MARKERS` matched nothing: `unreadable` stayed
    empty, nothing warned, and the pin held HEAD's bytes for the lane's only copy at exit 0 —
    the silent-HEAD shape this module forbids. Reproduced with `chmod 0644 d/` over a
    modified `d/f.txt`: stderr was that one line and `pin` printed `pinned 2 file(s)` with no
    warning at all.
    """
    (hub / "d").mkdir()
    (hub / "d" / "f.txt").write_text("v1\n")
    assert git("add", "-A", cwd=hub).returncode == 0
    assert git("commit", "-qm", "d", cwd=hub).returncode == 0

    wt = clean_lane(hub, "wt-permden")
    (wt / "d" / "f.txt").write_text("THE LANE'S ONLY COPY\n")
    (wt / "tracked.txt").write_text("another edit\n")  # keeps the pin off the clean path
    (wt / "d").chmod(0o644)  # read, no EXECUTE: git cannot open the file and says so BARE
    try:
        if os.access(wt / "d" / "f.txt", os.R_OK):
            pytest.skip(
                "the path is readable to this user (root), so git would not skip it and "
                "the bare-denial line under test never appears"
            )
        pinned = run_tool("pin", cwd=wt)
    finally:
        (wt / "d").chmod(0o755)

    combined = pinned.stdout + pinned.stderr
    assert "d/f.txt" in combined, (
        f"a path git could not index must be ANNOUNCED by name, never pinned as HEAD: {combined!r}"
    )
    assert "WARNING" in combined or pinned.returncode != 0, combined


def test_git_is_asked_for_english_diagnostics(hub: Path, tmp_path: Path) -> None:
    """Every skip marker this module parses is git's own PROSE, so the locale must be pinned.

    `strerror` strings are gettext-translated. Measured through a shim that kept real git and
    translated only `Permission denied`: `pin` exited 0 with an EMPTY stderr and held HEAD's
    bytes for a path inside a no-execute directory, while the disk held the lane's only copy.
    `git status` does not list that path at all, so `_skipped_paths` is the ONLY detector for
    that shape — a host-locale-dependent guard is a guard that is absent on most hosts.
    """
    real = shutil.which("git")
    assert real, "git must be on PATH"
    shim = tmp_path / "locale-shim"
    shim.mkdir()
    record = tmp_path / "locale.txt"
    (shim / "git").write_text(
        f'#!/bin/sh\nprintf "%s|%s\\n" "$LC_ALL" "$LANGUAGE" >> "{record}"\nexec "{real}" "$@"\n'
    )
    (shim / "git").chmod(0o755)

    wt = clean_lane(hub, "wt-locale")
    (wt / "tracked.txt").write_text("the lane's work\n")

    env = dict(os.environ)
    env["PATH"] = f"{shim}{os.pathsep}{env.get('PATH', '')}"
    # A HOSTILE AMBIENT LOCALE: the tool must override it, not inherit it.
    env["LC_ALL"] = "de_DE.UTF-8"
    env["LANGUAGE"] = "de"
    cp = subprocess.run(
        [PYTHON, str(TOOL), "pin"],
        cwd=str(wt),
        capture_output=True,
        text=True,
        errors="surrogateescape",
        env=env,
        check=False,
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr

    seen = {ln.strip() for ln in record.read_text().splitlines() if ln.strip()}
    assert seen, "the shim never ran: the tool did not invoke git at all"
    assert all(s.startswith("C|") for s in seen), (
        "git must be asked for ENGLISH diagnostics (LC_ALL=C, LANGUAGE unset) whatever the "
        f"host locale, or every skip marker is unmatched: {seen}"
    )


def test_a_dangling_symlink_does_not_refuse_a_genuine_pin(hub: Path) -> None:
    """`hash-object <path>` on a dangling link FAILS, so the link must be hashed by its target.

    With the link read through `hash-object`, the completeness cross-check saw a failed read
    and refused a pin that held every byte (a false refusal, and one that pushes a lane into
    `--force`). The correct comparison is the LINK TEXT, which is what git stores for mode
    120000.
    """
    wt = clean_lane(hub, "wt-dangling-pin")
    (wt / "tracked.txt").write_text("the lane's real work\n")
    os.symlink("nowhere-at-all", wt / "dangling")
    # ⛔ AND A LIVE LINK: `hash-object <path>` follows it and answers with the TARGET's
    # bytes, which never equals the link-text blob — so without the symlink branch the
    # comparison reports a false "does not hold" over a link the pin recorded perfectly.
    os.symlink("tracked.txt", wt / "link")
    status = git("status", "--porcelain", cwd=wt).stdout
    assert "dangling" in status, status

    out = run_tool("pin", cwd=wt)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "does NOT hold" not in out.stderr, out.stderr
    ref = pin_ref(out.stdout)
    assert (git("show", f"{ref}:dangling", cwd=hub).stdout) == "nowhere-at-all", (
        "the pin must record the LINK TEXT for a dangling symlink"
    )
