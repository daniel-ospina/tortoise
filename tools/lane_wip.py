#!/usr/bin/env python3
"""lane_wip — a lane's UNCOMMITTED work, made durable OUTSIDE its worktree (#10812).

WHY THIS EXISTS (measured, not inferred)
----------------------------------------
`git worktree remove --force` on a DIRTY worktree returns **0** and destroys the
uncommitted work irrecoverably — the content was never in the object DB, so no
route gets it back. Measured in a temp repo (2026-11-04):

    worktree remove --force rc= 0
    dir exists: False      admin record exists: False
    branch tip files: ['f.txt']        -> big.py (4,500 uncommitted lines) GONE

#10812 is that loss happening for real: lane `p1-10538` had ~4,500 uncommitted
lines when its worktree disappeared under a live reviewer process. It was
recoverable **only by accident**, from a diff snapshot written to `/tmp` for that
reviewer. The fleet's logs were silent — neither logged reaper touched it, and the
remover was a third path that nobody could name.

⛔ THE BOARD ALREADY NOTICES; THE LANE STILL LOSES THE WORK. `fleet_state` sees a
removed directory under a live pid — `process_cwds` answers
`refused={<pid>: '<path> (deleted)'}` (the #2401 `CwdMap`, from the 2026-10-24
cell-4 incident) — so "make the lane notice" is DELIVERED and must not be
re-implemented here. Noticing is not recovering: the board names the catastrophe
after the content is already unreachable.

So the fix belongs at the point the content leaves the filesystem. This tool pins
the working state into a REF (so `gc` cannot take it), which makes the loss
**recoverable whoever removed the tree** — a property no amount of remover-logs
would give, because the remover was never identified.

⛔ IT IS RECOVERABLE, NOT IMPOSSIBLE, AND NOTHING RUNS `pin` FOR YOU. There is no
hook, no reaper integration and no dispatch wiring (measured: nothing outside
`tools/lane_wip.py` and its test names this tool), and at least two deployed
removers DO force-remove dirty worktrees without pinning first. So a lane that
never pinned still loses its work exactly as #10812 describes. The claim this tool
supports is narrow and it is the true one: **a pinned lane's work survives any
removal**. Wiring `pin` into the lane-dispatch path is a separate change.

THE CONTRACT
------------
* **`pin` is ZERO-DISRUPTION.** It never touches `HEAD`, the real index or the
  working tree. The snapshot is built from a SCRATCH index
  (`GIT_INDEX_FILE`), so a lane keeps working with its dirty tree exactly as it
  was. Measured: `git status --porcelain`, the bytes of `.git/index` and `HEAD`
  are all identical across a pin. (This is why `git stash` is not the primitive:
  `stash push` *removes* the changes, and `stash create -u` silently ignores
  `-u`, so untracked files are lost — both measured in `tools/lane_stash.py`.)
* **Fidelity is the working state, not the commit.** Tracked modifications,
  untracked (non-ignored) files AND deletions are all captured; ignored files are
  not (a lane's `.venv`/`WORKTREE-REPORT.md` are not work).
* **`refs/pi-lane-wip/<lane-key>/<n>`, deliberately OUTSIDE `refs/lane`.**
  `lane_stash._lane_key_of_ref` treats every `refs/lane/<key>/…` ref as lane-owned,
  so a WIP pin there would change `lane_stash`'s ownership reasoning for stashes. This
  is the same reason `lane_stash` keeps its prune guard at
  `refs/pi-lane-prune-guard`. The lane key is `lane_stash.lane_key()` — the SAME
  identity, so a lane is one lane across both tools (worktree-unique and
  branch-independent).
* **`recover` refuses to overwrite live work.** Restoring is `read-tree --reset
  -u`, which discards the target's uncommitted **tracked** state (an unrelated
  untracked file in the target survives), so a dirty target is refused unless
  `--force`. A recovery tool that eats the work it was asked to save is the failure
  it exists to prevent.

USAGE
-----
    tools/lane_wip.py pin                       # pin this worktree's dirty state
    tools/lane_wip.py pin --note "before rebase"
    tools/lane_wip.py list                      # this lane's pins
    tools/lane_wip.py list --all                # every lane's pins
    tools/lane_wip.py recover <ref|sha> --into <path> [--branch <b>] [--force]
    tools/lane_wip.py recipe                    # the recovery recipe (#10812 ask)

Exit codes: 0 ok · 2 usage/state error · 3 a git operation failed.
"""

from __future__ import annotations

import sys

# #5128 shape: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.12+-only import would otherwise fail first, and it would fail with a traceback
# instead of this message. The gate that enforces this shape is
# tests/test_entry_point_python_guard.py, not a style rule.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # Exit 2, not `SystemExit(<str>)` (which always exits 1): 2 is this tool's own
    # environment/state-error code, and 1 is reserved so it can never be confused
    # with a verdict about a lane's work.
    print(
        f"tools/lane_wip.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/lane_wip.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# The lane identity and the retargeting-env scrubber are REUSED, never copied: a lane
# must be the same lane for its stash pins and its wip pins, and a second copy of
# `_env` (which strips GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE so an inherited hook
# cannot retarget us at a peer checkout) would drift from the one that is tested.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lane_stash

WIP_NS = "refs/pi-lane-wip"
#: Pin ids per lane key. A lane that pins before every review dispatch reaches this
#: inside one lane lifetime, and then its PRIMARY verb (make the work safe) is
#: unavailable — so the ceiling is stated in the module docstring and in the error,
#: with the exact command that clears it. There is no prune verb: pins are durable on
#: purpose, and silently expiring them would recreate the loss this tool prevents.
MAX_ID_TRIES = 50
#: The snapshot commit is written with an explicit identity so a repo whose
#: `user.name` is unset (a fresh CI/fixture clone) can still pin.
_IDENTITY = {
    "GIT_AUTHOR_NAME": "lane-wip",
    "GIT_AUTHOR_EMAIL": "lane-wip@localhost",
    "GIT_COMMITTER_NAME": "lane-wip",
    "GIT_COMMITTER_EMAIL": "lane-wip@localhost",
}


class LaneWipError(Exception):
    """A state/usage error (exit 2) — never raised for a plain git failure."""


class GitFailed(Exception):
    """A git operation failed (exit 3), carrying its own returncode."""

    def __init__(self, rc: int, message: str):
        super().__init__(message)
        self.rc = rc


def _git(
    *args: str,
    check: bool = True,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """`git`, with an extra env layer and `-C cwd` when a caller names one."""
    env = lane_stash._env()
    env.update(extra_env or {})
    env.update(_IDENTITY)
    cmd = ["git", *(["-C", str(cwd)] if cwd is not None else []), *args]
    cp = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
        check=False,
    )
    if check and cp.returncode != 0:
        raise GitFailed(cp.returncode, (cp.stderr or cp.stdout).strip())
    return cp


def _out(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    return _git(*args, check=check, cwd=cwd).stdout.strip()


def _out_z(*args: str, cwd: Path | None = None, check: bool = True) -> list[str]:
    """A `-z` path list, NUL-split. ⛔ NEVER through `strip()`.

    `str.strip()` eats LEADING whitespace off the whole NUL-joined stream — which is the
    FIRST path — so a lane with a file called ` lead.txt` had that path silently absent
    from the snapshot's file list: `recover` then reported "1 of 2 file(s) … ` lead.txt`
    did NOT appear" over a byte-perfect restore (rc=3), and `_intent_to_add` announced a
    properly staged ` lead.txt` as "NOT in this pin". Only trailing NULs are separators;
    the bytes before them are the filename.
    """
    raw = _git(*args, check=check, cwd=cwd).stdout
    return [p for p in raw.rstrip("\0").split("\0") if p]


def wip_ns(key: str) -> str:
    return f"{WIP_NS}/{key}"


# --------------------------------------------------------------------------- pin


def _worktree_root(path: Path) -> str:
    """The worktree ROOT containing ``path``, or ``""`` when it is not a work tree.

    ⛔ A SUBDIRECTORY IS NOT A LANE. `rev-parse --is-inside-work-tree` is true for
    any directory inside one, and the lane key is derived from the path given — so
    `pin --path <worktree>/src` minted `…/src-<digest>/n`, a DIFFERENT key from the
    worktree's own, and `list` at the root then answered "lane … has no wip pin".
    That is the false "the work is gone" verdict the RECIPE path must never give.
    """
    cp = _git("rev-parse", "--show-toplevel", check=False, cwd=path)
    return cp.stdout.strip() if cp.returncode == 0 else ""


def _index_tree(root: Path) -> tuple[str, str]:
    """``(tree, "")`` for the REAL index, or ``("", why)`` when it cannot be read.

    ⛔ THE INDEX IS PART OF THE WORKING STATE, AND IT CAN HOLD WORK NOTHING ELSE
    HOLDS. A `MM` file has a staged version and a different worktree version; an
    `AD` file's blob exists ONLY in the index. A snapshot built from `HEAD` + the
    worktree (`read-tree HEAD` + `add -A`) sees neither, so the tool reported
    "clean — nothing to pin" for a tree `git status` called dirty — the worst
    verdict a recovery tool can give. `write-tree` is READ-ONLY: it renders the
    current index as a tree object without touching the index or the worktree.
    """
    # ⛔ RENDER FROM A COPY OF THE INDEX. `git write-tree` needs `.git/index.lock` (it
    # writes the cache-tree extension) and `GIT_OPTIONAL_LOCKS=0` does NOT exempt it —
    # measured: with the lock held it fails `Unable to create index.lock`, while the same
    # call against a COPY returns rc 0. So ANY concurrent `git add`/`commit`/`stash`/`pin`
    # in the lane made the staged half silently absent from the pin at rc 0 — and for an
    # `AD` file the index is the ONLY copy of that content. Copying also means a pin never
    # TAKES the lock, which is what "zero-disruption" was claiming.
    index_path = _out(
        "rev-parse", "--path-format=absolute", "--git-path", "index", check=False, cwd=root
    )
    if index_path and Path(index_path).is_file():
        with tempfile.TemporaryDirectory(prefix="lane-wip-rtree-") as td:
            copy = Path(td) / "index"
            shutil.copy2(index_path, copy)
            cp = _git("write-tree", check=False, cwd=root, extra_env={"GIT_INDEX_FILE": str(copy)})
    else:
        cp = _git("write-tree", check=False, cwd=root)
    if cp.returncode != 0:
        return "", (cp.stderr or cp.stdout).strip() or "no output"
    # ⛔ A NON-EMPTY STDERR IS A SIGNAL EVEN ON rc 0. `git write-tree` exits 0 while
    # printing `error: cache entry has null sha1:` on a truncated index (measured), so
    # keying on rc alone pinned a garbled index and announced it as "staged state is
    # pinned too". The tree it returned in that state must not be trusted.
    noise = (cp.stderr or "").strip()
    if noise:
        return "", noise
    return cp.stdout.strip(), ""


def _warn_about_gitlinks(tree: str, *, root: Path) -> None:
    """Say so when the pin contains NESTED REPOS, whose content it cannot hold.

    A nested repo is recorded as a single gitlink entry (mode 160000) naming a commit that
    lives in the NESTED `.git`. Nothing in this pin carries that object, so after the outer
    worktree is destroyed the nested repo's COMMITTED content is unrecoverable — and its
    uncommitted work was never in the tree at all. Measured: `git add -A` printed
    "adding embedded git repository" and the tool dropped the warning, so `pin` and
    `recover` both reported success over content that could not come back.
    """
    entries = _out_z("ls-tree", "-r", "-z", tree, check=False, cwd=root)
    links = [ln.split("\t", 1)[1] for ln in entries if ln.startswith("160000 ") and "\t" in ln]
    if links:
        print(
            f"lane_wip: WARNING — {len(links)} nested repository/ies are recorded as a "
            f"gitlink only ({', '.join(links[:5])}{' …' if len(links) > 5 else ''}); their "
            "own commits and uncommitted work are NOT in this pin and cannot be recovered "
            "from it. Copy them out before anything removes this worktree",
            file=sys.stderr,
        )


def _intent_to_add(root: Path, index_tree: str) -> list[str]:
    """Index paths that `git write-tree` OMITS — intent-to-add entries.

    ⛔ `git write-tree` EXITS 0 AND SAYS NOTHING while leaving every `git add -N` entry
    out of the tree (measured: the index holds `ita.py` at the empty blob, the written tree
    equals `HEAD^{tree}`). So the index view compares EQUAL to HEAD, the pin records no
    staged half, no warning fires, and `recover` turns the lane's ` A ita.py` into
    `?? ita.py` — after which the lane's `git commit -a` silently omits a file it would
    have landed. Detected by set difference, and reported LOUDLY: the entry cannot be
    reconstructed from the pin, so silence here would be a false promise.
    """
    staged = _out_z("ls-files", "--stage", "-z", check=False, cwd=root)
    in_index = set()
    for entry in staged:
        if "\t" in entry:
            in_index.add(entry.split("\t", 1)[1])
    if not in_index:
        return []
    in_tree = {p for p in _out_z("ls-tree", "-r", "-z", "--name-only", index_tree, cwd=root)}
    return sorted(in_index - in_tree)


def snapshot(root: Path, *, branch: str, note: str = "") -> tuple[str, list[str], str]:
    """``(commit, files, index_tree)`` for the working state; ``("", [], "")`` if clean.

    THREE states are captured, because a git tree alone cannot hold them:

    * ``worktree_tree`` — from a SCRATCH index (`read-tree HEAD` + `add -A`), so a
      file the lane DELETED is recorded as deleted and untracked files are included.
      The real index is never opened for this, which is what makes `pin`
      zero-disruption; the scratch file is forced OUTSIDE the worktree (an inherited
      `TMPDIR=<worktree>/tmp` otherwise put it in the tree and `add -A` snapshotted
      the tool's own index into the pin).
    * ``index_tree`` — the REAL index, read-only (§`_index_tree`).
    * the pin is CLEAN only when BOTH equal `HEAD`'s tree, so a staged-only change
      can no longer be reported as "nothing to save".

    ``index_tree`` is returned so `recover` can restore the staged/unstaged SPLIT,
    not just the content: a lane that had staged part of its work must get that split
    back, or its next bare `git commit` (which would have committed the staged part)
    commits nothing.
    """
    head = _git("rev-parse", "HEAD", check=False, cwd=root)
    if head.returncode != 0:
        raise LaneWipError(
            f"{root} has no HEAD commit — a lane's work cannot be described against "
            "an empty history; commit once, then pin"
        )
    head_sha = head.stdout.strip()
    head_tree = _out("rev-parse", "HEAD^{tree}", cwd=root)
    index_tree, index_error = _index_tree(root)

    scratch_dir = tempfile.mkdtemp(prefix="lane-wip-index-")
    if Path(scratch_dir).resolve().is_relative_to(root.resolve()):
        shutil.rmtree(scratch_dir, ignore_errors=True)
        scratch_dir = tempfile.mkdtemp(prefix="lane-wip-index-", dir=root.resolve().parent)
        if Path(scratch_dir).resolve().is_relative_to(root.resolve()):
            shutil.rmtree(scratch_dir, ignore_errors=True)
            raise LaneWipError(
                f"no temp directory outside {root} to hold the scratch index — every "
                "candidate (TMPDIR and the worktree's parent) resolves inside the "
                "worktree, so a pin here would snapshot its own scratch state"
            )
    try:
        scratch = str(Path(scratch_dir).resolve() / "index")
        env = {"GIT_INDEX_FILE": scratch}
        _git("read-tree", "HEAD", cwd=root, extra_env=env)
        add = _git("add", "-A", check=False, cwd=root, extra_env=env)
        if add.returncode != 0:
            raise GitFailed(
                add.returncode,
                f"`git add -A` against a scratch index failed: "
                f"{(add.stderr or add.stdout).strip()}",
            )
        write = _git("write-tree", check=False, cwd=root, extra_env=env)
        # ⛔ THE SAME STDERR RULE AS `_index_tree`: `write-tree` exits 0 while printing
        # `error: cache entry has null sha1:` on a truncated index, so a garbled tree
        # would become the pin's WORKTREE view — the one thing that is supposed to be
        # reliable. Keying on rc alone is not enough, here or there.
        noise = (write.stderr or "").strip()
        if write.returncode != 0 or noise:
            raise GitFailed(
                write.returncode or 1,
                f"`git write-tree` did not produce a trustworthy tree: "
                f"{noise or (write.stderr or write.stdout).strip()}",
            )
        tree = write.stdout.strip()
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)

    # ⛔ CLEAN MEANS BOTH VIEWS AGREE WITH HEAD. An index that could not be read is
    # NOT clean: "unknown" must never render as "nothing to save".
    worktree_clean = tree == head_tree
    if index_tree:
        ita = _intent_to_add(root, index_tree)
        if ita:
            shown = ", ".join(ita[:5]) + (" …" if len(ita) > 5 else "")
            print(
                f"lane_wip: WARNING — {len(ita)} intent-to-add path(s) are NOT in this pin "
                f"({shown}). `git write-tree` omits them silently, so they cannot be "
                "restored from it; run `git add <path>` (or `git rm --cached <path>`) and "
                "pin again if that content matters",
                file=sys.stderr,
            )
        if worktree_clean and index_tree == head_tree:
            return "", [], ""
    else:
        # ⛔ A MISSING SECOND VIEW IS NEVER SILENT. Reported only in the clean branch,
        # the ordinary DIRTY case pinned with rc 0 and not one word that the staged half
        # was absent — an unmerged index (`write-tree` refuses with rc 128) is exactly
        # what a lane pins before resolving a conflict. The pin is still useful (the
        # worktree view is real), so this is a loud warning rather than a refusal.
        print(
            f"lane_wip: WARNING — the index of {root} could not be captured "
            f"({index_error}); the STAGED state is NOT in this pin, only the working "
            "tree. Resolve the index and pin again if you need the split restored",
            file=sys.stderr,
        )
        if worktree_clean:
            raise LaneWipError(
                f"the index of {root} could not be read ({index_error}), so "
                "'nothing to pin' cannot be claimed — resolve the index, then pin"
            )

    # ⛔ `-z`, NEVER LINE-SPLIT. `ls-tree --name-only` runs paths through
    # `core.quotePath`, so `café.py` comes back as the literal string
    # `"caf\303\251.py"` — quotes and octal escapes included. Counting them was
    # harmless, but the RECOVERY VERIFICATION below tests each path on disk, so a
    # non-ASCII name (or one containing a tab or newline) read as missing: a byte-perfect
    # restore reported as a failure, with a remedy that cannot work.
    files = _out_z("ls-tree", "-r", "-z", "--name-only", tree, cwd=root)
    _warn_about_gitlinks(tree, root=root)
    # The branch and the note live in the COMMIT: git keeps NO reflog for a custom
    # namespace (`core.logAllRefUpdates` covers refs/heads, refs/remotes, refs/notes
    # and HEAD), so an `update-ref -m` message is silently DISCARDED — measured.
    message = f"lane-wip: {root.name} on {branch}"
    if note:
        message += f"\n\n{note}"
    message += f"\n\npinned at {datetime.now(UTC).isoformat(timespec='seconds')}"

    # ⛔ THE INDEX TREE MUST BE REACHABLE, NOT MERELY WRITTEN DOWN. As a commit-message
    # TRAILER it was a P1 hole: a message is not a reachability root, so
    # `git gc --prune=now` collected the index tree and its blobs — precisely the view
    # that carries work nothing else holds (an `AD` shape's staged blob exists ONLY in
    # the index). Measured: pin an `AD`+`MM` tree, remove the worktree, gc, then
    # `recover` exited 3 with `fatal: failed to unpack tree object` and the
    # only-in-index file was gone. The ordinary path broke too: pin, commit, gc.
    #
    # A SECOND PARENT is a graph edge: gc keeps it, `rev-parse <pin>^2^{tree}` reads it
    # back with no prose parsing, and a caller's `--note` can no longer forge it (it
    # could, and a forged trailer made `recover` restore a DIFFERENT index while
    # claiming the split came back — measured).
    parents = ["-p", head_sha]
    if index_tree and index_tree != head_tree:
        index_commit = _git(
            "commit-tree",
            index_tree,
            "-m",
            f"lane-wip index: {root.name} on {branch}",
            cwd=root,
        ).stdout.strip()
        # (An `if not index_commit: raise` stood here and was REMOVED as inert:
        # `_git("commit-tree", ...)` runs with `check=True`, so a failure raises
        # `GitFailed` before this line and empty stdout with rc 0 is not producible.)
        parents += ["-p", index_commit]
    commit = _git("commit-tree", tree, *parents, "-m", message, cwd=root).stdout.strip()
    return commit, files, (index_tree if index_tree != head_tree else "")


def pin_ref(key: str, sha: str, *, cwd: Path) -> str:
    """Allocate ``refs/pi-lane-wip/<key>/<n>`` for ``sha`` by CAS create.

    `update-ref <ref> <new> ""` — an EMPTY old value means "the ref must not
    exist". Two lanes sharing a key (impossible by construction: the key carries a
    path digest) or two pins racing would otherwise silently overwrite, and the
    loser's work would be the thing lost.

    ⛔ `cwd` IS REQUIRED AND MUST BE THE PINNED WORKTREE. `update-ref` resolves the
    ref AND looks up the object in the repo it runs in, so writing from the caller's
    checkout against a `--path` in another repo answers
    ``cannot update ref … nonexistent object`` — the snapshot was built in B's repo
    and the ref was attempted in A's. `pin --path` is a supported shape (it is the
    remedy `recover` prints), so this had to carry its own repo.
    """
    for n in range(1, MAX_ID_TRIES + 1):
        ref = f"{wip_ns(key)}/{n}"
        cp = _git("update-ref", ref, sha, "", check=False, cwd=cwd)
        if cp.returncode == 0:
            return ref
        # A collision (the ref exists) is the only expected failure; anything else
        # is a real error and must not be retried into a misleading success.
        if _git("show-ref", "--verify", "--quiet", ref, check=False, cwd=cwd).returncode != 0:
            raise GitFailed(
                cp.returncode,
                f"could not create {ref}: {(cp.stderr or cp.stdout).strip()}",
            )
    raise LaneWipError(
        f"{MAX_ID_TRIES} pin ids for lane {key} are already taken — the registry is "
        "full; clear old pins with `git update-ref -d <ref>` once their work is safe"
    )


def cmd_pin(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve() if args.path else lane_stash.repo_root()
    if root.exists() and not root.is_dir():
        raise LaneWipError(f"{root} is a file, not a worktree — nothing to pin there")
    if not root.is_dir():
        # ⛔ NEVER PIN A GHOST. This is the state a lane is in when it FINALLY
        # notices the loss, and a tool that quietly pinned the caller's cwd instead
        # would tell that lane its work is safe while nothing was saved.
        raise LaneWipError(
            f"{root} is not a directory — that worktree is gone, so there is nothing "
            "left to pin. A pin must be taken BEFORE the removal; "
            "`tools/lane_wip.py recipe` says what is still recoverable"
        )
    # A subdirectory is not a lane — see `_worktree_root`: keying on it mints a
    # DIFFERENT lane key, so `list` at the worktree root then answered "no pin here".
    root = Path(_worktree_root(root) or root).resolve()
    key = lane_stash.lane_key(root)
    # ⛔ A NON-REPO DIRECTORY IS A STATE ERROR, NOT A GIT FAILURE (exit 2, not 3).
    # `is_dir()` alone let `--path /tmp` reach a `rev-parse` and surface git's own
    # `fatal: not a git repository` with rc 3 — a message that names neither the
    # caller's mistake nor the remedy.
    inside = _git("rev-parse", "--is-inside-work-tree", check=False, cwd=root)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise LaneWipError(f"{root} is not a git work tree — nothing to pin there")
    # `check=False`: an unborn HEAD (a fresh `git init` with no commit) is reported by
    # `snapshot` below, which owns that message.
    branch = _out("rev-parse", "--abbrev-ref", "HEAD", cwd=root, check=False) or "HEAD"
    sha, files, index_tree = snapshot(root, branch=branch, note=args.note)
    if not sha:
        print(f"clean — nothing to pin in {root} (working tree matches HEAD)")
        return 0
    ref = pin_ref(key, sha, cwd=root)
    print(f"pinned {len(files)} file(s) from {root}")
    print(f"  ref    {ref}")
    print(f"  sha    {sha}")
    print(f"  branch {branch}")
    if args.note:
        print(f"  note   {args.note}")
    if index_tree:
        print(f"  index  {index_tree} (staged state is pinned too)")
    # ⛔ A DETACHED PIN HAS NO BRANCH TO NAME. Printing `--branch HEAD` gave a remedy that
    # is REFUSED as soon as the caller's HEAD moves (the stale guard compares the caller's
    # HEAD to the pin's parent), with advice that is wrong for this shape.
    handle = "" if branch == "HEAD" else f" --branch {branch}"
    print(f"recover with: tools/lane_wip.py recover {ref} --into <path>{handle}")
    return 0


# -------------------------------------------------------------------------- list


def _pin_id(ref: str) -> int:
    """The `<n>` of ``refs/pi-lane-wip/<key>/<n>``, for a NUMERIC sort.

    Sorting on the ref string alone puts `/10` BEFORE `/9` (lexicographic), so a lane
    with more than nine pins got the wrong "newest" from `| tail -1` whenever two pins
    shared a timestamp — which they do, because pins are seconds apart.
    """
    tail = ref.rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _commit_meta(sha: str, *, cwd: Path) -> tuple[str, str]:
    """``(when, subject)`` for a snapshot commit, tolerant of a damaged TREE.

    `git show -s` needs the tree to exist, so a pin whose tree object is gone lost its
    timestamp and subject as well — and a damaged pin is exactly the row an operator
    has to triage. The COMMIT object is still readable in that state, so fall back to
    `cat-file commit` and read the header/refmessage directly.
    """
    when = _out("show", "-s", "--format=%cI", sha, check=False, cwd=cwd)
    subject = _out("show", "-s", "--format=%s", sha, check=False, cwd=cwd)
    if when and subject:
        return when, subject
    raw = _git("cat-file", "commit", sha, check=False, cwd=cwd)
    if raw.returncode != 0:
        return when, subject
    header, _, message = raw.stdout.partition("\n\n")
    for line in header.splitlines():
        if line.startswith("committer "):
            parts = line.rsplit(" ", 2)
            if len(parts) == 3 and parts[1].isdigit():
                when = datetime.fromtimestamp(int(parts[1]), tz=UTC).isoformat(timespec="seconds")
            break
    first = next((ln.strip() for ln in message.splitlines() if ln.strip()), "")
    return when, subject or first


def _describe(ref: str, *, cwd: Path) -> dict:
    """One pin's row: ref, sha, when, the files it holds, and its own message.

    ⛔ A BROKEN PIN IS NEVER REPORTED AS AN EMPTY ONE. Two distinct failures are both
    real and both must be named: the ref resolves to no object at all, and the object
    resolves but its tree is unreadable (a damaged or partially-pruned object DB — the
    disaster case this tool exists for). `_out(..., check=False)` yields "" for ANY
    failure, so a count taken from an unchecked read turned a damaged pin into
    `0 file(s)` with rc 0 — "this pin holds nothing", which is the one conclusion that
    would make a lane stop looking.
    """
    sha = _out("rev-parse", "--verify", "--quiet", ref, check=False, cwd=cwd)
    row = {"ref": ref, "sha": sha, "when": "", "note": "", "files": 0, "damaged": False}
    if not sha:
        row["damaged"] = True
        row["note"] = f"{ref} — DANGLING: the ref names no object; it holds no work"
        return row
    if _git("cat-file", "-e", sha, check=False, cwd=cwd).returncode != 0:
        row["damaged"] = True
        row["note"] = (
            f"{ref} — DAMAGED: the commit object {sha[:12]} is unreadable, so this "
            "pin's content cannot be inspected; it may still be restorable from another "
            "clone of this repo"
        )
        return row

    row["when"], row["note"] = _commit_meta(sha, cwd=cwd)
    row["note"] = row["note"] or ref

    # The note rides in the commit BODY — a ref-update message is discarded (git keeps
    # no reflog for a custom namespace), so this is where `list` must read it.
    # ⛔ THE TRAILER IS STRIPPED BY POSITION, NOT BY PREFIX. `snapshot` writes
    # `<note>\n\npinned at <ts>` with the trailer LAST, and a caller's note is free to
    # begin with "pinned at ..." — filtering on that prefix silently dropped such a
    # note and kept the tool's own boilerplate instead.
    body_lines = _out("show", "-s", "--format=%b", sha, check=False, cwd=cwd).splitlines()
    if not body_lines:
        raw = _git("cat-file", "commit", sha, check=False, cwd=cwd)
        _, _, message = raw.stdout.partition("\n\n")
        body_lines = message.splitlines()[1:]
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    # ⛔ THERE IS EXACTLY ONE TRAILER, and it is stripped by POSITION — never by a
    # prefix a caller's own note may use. `snapshot` writes `<subject>\n\n<note>\n\n
    # pinned at <ts>` and NOTHING after it: the staged half is the pin's SECOND PARENT
    # (a graph edge), never a prose `index-tree <sha>` line (`_pin_index_tree`).
    # ⚠️ A second entry here would be DEAD, not merely redundant. The separator BLANK
    # LINE `%b` leaves between the note and `pinned at` is the last line once the
    # trailer is popped, so it is what shields a caller — and it shields a note whose
    # last line is a former trailer word too (MEASURED on the pre-fix head:
    # `--note $'first line\nindex-tree deadbeef'` lists as "first line / index-tree
    # deadbeef"). Keeping the entry would therefore buy no safety while asserting a
    # strip that cannot fire — an inaccurate claim in a file this PR adds. So it is
    # removed, and the last-line case is pinned for BOTH former words by
    # `test_a_note_that_starts_with_the_trailer_words_survives`.
    if body_lines and body_lines[-1].startswith("pinned at "):
        body_lines.pop()
    note_text = "\n".join(body_lines).strip()
    if note_text:
        # One line per pin keeps the list greppable and `tail -1`-able, so a
        # multi-line note is folded rather than allowed to break the format.
        folded = " / ".join(ln.strip() for ln in note_text.splitlines() if ln.strip())
        row["note"] = f"{row['note']} — {folded}"

    # `ls-tree` is what actually fails on a missing tree, and it fails FIRST, so its
    # own status is the signal — a separate existence probe can succeed on a tree git
    # could not list.
    listing = _git("ls-tree", "-r", "--name-only", sha, check=False, cwd=cwd)
    if listing.returncode != 0:
        row["damaged"] = True
        row["note"] = (
            f"{row['note']} — DAMAGED: the tree object is unreadable "
            f"({(listing.stderr or listing.stdout).strip() or 'no output'}), so the "
            "file count is UNKNOWN, not 0"
        )
        return row
    row["files"] = len([ln for ln in listing.stdout.splitlines() if ln.strip()])
    return row


def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve() if args.path else lane_stash.repo_root()
    # ⛔ THE PATTERN IS A PREFIX, NOT `refs/pi-lane-wip/*`. `for-each-ref` matches with
    # `WM_PATHNAME`, so `*` does NOT cross `/` — and pins live TWO components deep
    # (`<key>/<n>`), so the glob form matched NOTHING and `list --all` answered "no
    # lane has any wip pin" while the refs sat right there. That is RECIPE step 1, so
    # the tool was telling the disaster victim its pinned work was gone.
    # ⛔ THE READ SIDE NEEDS THE SAME NORMALISATION AS THE WRITE SIDE. `pin` keys on the
    # WORKTREE ROOT; `list` keyed on the raw `--path`, so after `pin --path <wt>/pkg`
    # (accepted, keyed on the worktree) `list --path <wt>/pkg` answered "lane pkg-… has
    # no wip pin" — the false "the work is gone" verdict this tool must never give.
    # ⛔ A DESTROYED PATH IS THE STATE THIS TOOL EXISTS FOR, so `list` must answer rather
    # than surface git's `fatal: cannot change to …` as "the registry is unreadable". The
    # refs live in the CALLER's repo; `lane_key` still derives from the dead path, so
    # `--all` (and a lane-scoped read) work without the directory.
    if not Path(root).is_dir():
        if not args.all:
            raise LaneWipError(
                f"{root} does not exist — that worktree may have been removed. "
                "`tools/lane_wip.py list --all` reads the pins from this repo instead, "
                "and `tools/lane_wip.py recipe` says what is still recoverable"
            )
        root = lane_stash.repo_root()
    root = Path(_worktree_root(root) or root).resolve()
    pattern = f"{WIP_NS}/" if args.all else f"{wip_ns(lane_stash.lane_key(root))}/"
    cp = _git("for-each-ref", "--format=%(refname)", pattern, check=False, cwd=root)
    if cp.returncode != 0:
        raise GitFailed(
            cp.returncode,
            "could not read the wip refs (`git for-each-ref` failed): "
            f"{(cp.stderr or cp.stdout).strip() or 'no output'}",
        )
    refs = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
    if not refs:
        scope = (
            "no lane has any wip pin"
            if args.all
            else (
                f"lane {lane_stash.lane_key(root)} has no wip pin — "
                "run `tools/lane_wip.py pin` before anything that could remove this worktree"
            )
        )
        print(scope)
        return 0
    # Newest LAST, so the last line is the most recent pin and a lane under time
    # pressure can take it with `| tail -1`.
    rows = sorted(
        (_describe(r, cwd=root) for r in refs),
        key=lambda r: (r["when"], _pin_id(r["ref"])),
    )
    for row in rows:
        count = "UNKNOWN (damaged)" if row.get("damaged") else f"{row['files']} file(s)"
        print(f"{row['ref']}  {row['sha'][:12] or '-'}  {row['when']}  {count}")
        print(f"    {row['note']}")
    return 0


# ----------------------------------------------------------------------- recover


def _resolve_commit(target: str, *, cwd: Path) -> str:
    """A pin ref or a sha (prefix) → a full commit sha, or a state error."""
    cp = _git("rev-parse", "--verify", f"{target}^{{commit}}", check=False, cwd=cwd)
    if cp.returncode != 0 or not cp.stdout.strip():
        raise LaneWipError(
            f"{target!r} does not name a commit in this repository — "
            "`tools/lane_wip.py list --all` shows every pin"
        )
    return cp.stdout.strip()


def _target_is_dirty(path: Path) -> bool:
    """True when the target holds ANY local state a restore could overwrite.

    ⛔ IGNORED FILES COUNT. Plain `status --porcelain` does not see them, so a target
    whose only local content was gitignored (a local config, a `.env`) was judged
    CLEAN and the restore replaced it — exit 0, "recovered N file(s)", and a local
    secret gone. This repo's own teardown guard treats ignored-file destruction as
    destroy-worthy for the same reason (`scripts/cleanup-worktree.sh` passes
    `--ignored=traditional`).
    """
    # ⛔ `--untracked-files=normal` IS EXPLICIT ON PURPOSE. The guard's input must not be
    # settable by repo config: with `status.showUntrackedFiles=no` the plain read returned
    # `[]` for a target holding its own untracked `big.py`, so the restore overwrote it
    # without `--force` (measured) — the tool's central safety property, defeated by a
    # config entry.
    st = _git(
        "status",
        "--porcelain",
        "--ignored=traditional",
        "--untracked-files=normal",
        check=False,
        cwd=path,
    )
    if st.returncode != 0:
        raise GitFailed(
            st.returncode,
            f"could not read the state of {path}: {(st.stderr or st.stdout).strip()}",
        )
    if st.stdout.strip():
        return True

    # ⛔ `ASSume-UNCHANGED` IS INVISIBLE TO `status` AND IS OVERWRITTEN ANYWAY. A target
    # whose only local content was a modification to an assume-unchanged file read CLEAN,
    # and `read-tree --reset -u` replaced it — exit 0, no `--force`, the target's work
    # gone. This repo already records the same defeat elsewhere ("`git diff` is defeated by
    # assume-unchanged / skip-worktree"), so the guard must read the INDEX FLAGS, not just
    # `status`. `ls-files -v` prints a LOWERCASE tag for exactly those entries.
    flags = _git("ls-files", "-v", "-z", check=False, cwd=path)
    if flags.returncode != 0:
        raise GitFailed(
            flags.returncode,
            f"could not read the index flags of {path}: {(flags.stderr or flags.stdout).strip()}",
        )
    flagged = []
    for entry in flags.stdout.split("\0"):
        if not entry or " " not in entry:
            continue
        tag, rel = entry.split(" ", 1)
        # ⛔ skip-worktree PRINTS UPPERCASE `S`; only assume-unchanged is lowercased, so
        # `tag.islower()` missed the very case the message named. Fail closed: a
        # skip-worktree target is refused even when it looks clean.
        if tag.islower() or tag in ("S", "s"):
            flagged.append(rel)
    if flagged:
        shown = ", ".join(flagged[:5]) + (" …" if len(flagged) > 5 else "")
        raise GitFailed(
            3,
            f"{path} holds {len(flagged)} assume-unchanged/skip-worktree path(s) whose "
            f"local content `git status` cannot see ({shown}); restoring here would "
            "OVERWRITE them. Clear the flag (`git update-index --no-assume-unchanged "
            "<path>`) or restore into a new path",
        )
    return False


def _branch_holder(name: str, cwd: Path) -> tuple[str, str] | None:
    """``(worktree path, branch)`` for the worktree that has ``name`` checked out."""
    ref = name if name.startswith("refs/") else f"refs/heads/{name}"
    listing = _out("worktree", "list", "--porcelain", check=False, cwd=cwd)
    path = ""
    for line in listing.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.strip() == f"branch {ref}":
            return path, ref
    return None


def _branch_exists(name: str, cwd: Path) -> bool:
    """True when ``name`` resolves to a ref (a branch the reaper has not taken)."""
    ref = name if name.startswith("refs/") else f"refs/heads/{name}"
    return _git("show-ref", "--verify", "--quiet", ref, check=False, cwd=cwd).returncode == 0


def _pin_parent(sha: str, *, cwd: Path) -> str:
    """The commit the pin was taken ON — the branch tip at pin time (FIRST parent).

    DEFENCE IN DEPTH: `pin` always creates a first parent, so the empty return is
    unreachable from this tool's own pins; it exists for a pin written elsewhere or
    damaged. `cmd_recover` verifies the parent's OBJECT before using it.

    ⛔ ``--verify --quiet`` IS NOT DECORATION. Plain ``rev-parse`` echoes its unresolvable
    ARGUMENT to stdout on rc 128, so a parentless or damaged pin returned the literal
    string ``<sha>^1`` as the parent, and the failure surfaced three steps later as
    ``fatal: invalid reference: <sha>^1`` — a message naming neither the cause nor the
    remedy, on the path a lane takes when its work is already lost. ``--verify --quiet``
    prints nothing and returns rc 1, which is the "absent" contract this returns "" for.
    """
    return _out("rev-parse", "--verify", "--quiet", f"{sha}^1", check=False, cwd=cwd)


def _pin_index_tree(sha: str, *, cwd: Path) -> tuple[str, str]:
    """``(index tree, problem)`` — ``("", "")`` when the pin recorded no staged half.

    Read from the pin's SECOND PARENT — a graph edge, never a prose trailer. The
    trailer could be forged by a caller's `--note` (measured: a note containing
    `index-tree <sha>` made `recover` rebuild a DIFFERENT index and still report
    "with the staged/unstaged split it was pinned with"), and prose is not a gc root
    either, so the trailer's tree could be collected before it was ever read.
    """
    # ⛔ "ABSENT" AND "UNREADABLE" ARE DIFFERENT ANSWERS, AND BOTH USED TO BE ``""``.
    # A pin whose staged half had been damaged came back with the worktree restored, the
    # split silently missing, and `recovered N file(s)` on exit 0 — for an `AD` pin the
    # index tree holds the ONLY copy of that content, so that false success IS the loss
    # this tool exists to prevent.
    #
    # (A `declared >= 2` branch stood here and was REMOVED as inert: `rev-parse --verify
    # <sha>^2` answers with the RECORDED parent sha without checking that the object
    # exists, so it cannot distinguish "no second parent" from "second parent gone"; the
    # `^2^{tree}` check below is what actually catches that damage. Verified differentially
    # after deleting the second parent's commit object.)
    second = _git("rev-parse", "--verify", "--quiet", f"{sha}^2", check=False, cwd=cwd)
    if second.returncode != 0 or not second.stdout.strip():
        return "", ""
    tree = _out(
        "rev-parse",
        "--verify",
        "--quiet",
        f"{second.stdout.strip()}^{{tree}}",
        check=False,
        cwd=cwd,
    )
    if not tree:
        return "", (
            "the pin's second parent resolves but its TREE object is unreadable — the "
            "staged half of this pin cannot be restored"
        )
    return tree, ""


def _apply_staged_delta(base: str, want: str, *, target: Path) -> str:
    """Apply ONLY the paths where ``want`` differs from ``base`` — a delta, not a tree.

    ⛔ A WHOLE-TREE ``read-tree <pin's index tree>`` IS ONLY CORRECT WHILE THE TARGET IS AT
    THE PIN'S PARENT. ``index_tree`` is the index as it stood ON THE PIN'S BASE, so when
    the target's HEAD has ADVANCED (`--force` past the stale guard) writing that tree
    wholesale replaces the index with "pin's base + the pin's staged changes" — a CACHED
    REVERT of every commit made since the pin. Measured: the tool printed "recovered …
    with the staged/unstaged split it was pinned with", exited 0, and the lane's next bare
    `git commit` deleted a reviewed, committed file and the pin's own 4,500-line file off
    the branch. Applying the delta leaves the post-pin commits' entries intact.

    Returns ``""`` or a description of the first entry that could not be applied.
    """
    # ⛔ `-z` AND NUL-PAIR PARSING. With `--raw` alone, git quotes any path with a
    # non-ASCII byte, and the quoted STRING was passed straight to `update-index`, which
    # created an index entry whose FILENAME contains the quotes and octal escapes — then
    # the lane's next bare commit wrote that phantom into history (measured). `-z` emits
    # `<meta>\0<path>\0`, so a path can never be confused with a field.
    raw = _git(
        "diff-tree", "-r", "-z", "--no-renames", "--raw", base, want, check=False, cwd=target
    )
    if raw.returncode != 0:
        return f"could not read the pin's staged delta: {(raw.stderr or raw.stdout).strip()}"
    tokens = raw.stdout.split("\0")
    entries = []
    i = 0
    while i < len(tokens):
        meta = tokens[i]
        if meta.startswith(":") and i + 1 < len(tokens):
            entries.append((meta, tokens[i + 1]))
            i += 2
        else:
            i += 1
    for meta, path in entries:
        fields = meta.split()
        if len(fields) < 5 or not path:
            continue
        new_mode, new_sha, status = fields[1], fields[3], fields[4]
        if status.startswith("D"):
            cp = _git("update-index", "--force-remove", path, check=False, cwd=target)
        else:
            cp = _git(
                "update-index",
                "--add",
                "--cacheinfo",
                f"{new_mode},{new_sha},{path}",
                check=False,
                cwd=target,
            )
        if cp.returncode != 0:
            return f"could not stage {path}: {(cp.stderr or cp.stdout).strip()}"
    return ""


def cmd_recover(args: argparse.Namespace) -> int:
    here = Path(args.path).resolve() if args.path else lane_stash.repo_root()
    if getattr(args, "branch", None) == "HEAD":
        # `--branch HEAD` is what an older pin printed for a detached pin; treat it as
        # "no branch" rather than comparing the CALLER's HEAD to the pin's parent.
        args.branch = None
    sha = _resolve_commit(args.ref, cwd=here)
    if _git("cat-file", "-e", f"{sha}^{{tree}}", check=False, cwd=here).returncode != 0:
        # ⛔ "NO RECOVERABLE CONTENT" IS FALSE WHEN THE SECOND PARENT SURVIVES. A pin
        # whose worktree tree object was lost but whose staged half is readable still
        # holds the only copy of an `AD` file; the old message sent the lane to a dead
        # end in `recipe`. Name the salvage command instead.
        salvage = _out(
            "rev-parse", "--verify", "--quiet", f"{sha}^2^{{tree}}", check=False, cwd=here
        )
        if salvage:
            raise LaneWipError(
                f"{args.ref} resolves to {sha[:12]}, but its WORKTREE tree object is "
                f"unreadable. The STAGED half survives ({salvage[:12]}) — salvage it with "
                f"`git read-tree {sha}^2` in a scratch worktree, or read single files with "
                f"`git show {sha}^2:<path>`"
            )
        raise LaneWipError(
            f"{args.ref} resolves to {sha[:12]}, but its tree object is unreadable — "
            "the pin is damaged; neither its worktree nor its staged half is readable"
        )
    # `-z` for the same reason as in `snapshot`: these paths are TESTED ON DISK below.
    pin_files = [p for p in _out_z("ls-tree", "-r", "-z", "--name-only", sha, cwd=here)]
    parent = _pin_parent(sha, cwd=here)
    if not parent:
        # ⛔ WITHOUT A FIRST PARENT THERE IS NOTHING TO DETACH *ON TOP OF*, AND `or sha`
        # SILENTLY MADE THE SNAPSHOT THE BASE: `worktree add --detach <sha>` put HEAD ON
        # the snapshot, so `read-tree --reset -u <sha>` was a no-op, `git status` read
        # CLEAN, and the tool still printed `recovered N file(s)` — the precise failure
        # this file documents as fixed, reintroduced through the fallback. A pin written
        # by this tool always has a first parent, so a parentless one is foreign or
        # damaged: refuse it and name the route that does work.
        raise LaneWipError(
            f"{args.ref} resolves to {sha[:12]}, which has no first parent — it is a "
            "foreign or damaged pin, and restoring it would detach at the snapshot itself, "
            "leaving a CLEAN tree that looks like nothing was recovered. Salvage it "
            f"explicitly: in a scratch worktree run `git read-tree {sha[:12]}`, or read "
            f"single files with `git show {sha[:12]}:<path>`"
        )
    if _git("cat-file", "-e", f"{parent}^{{commit}}", check=False, cwd=here).returncode != 0:
        # ⛔ `rev-parse --verify <sha>^1` RETURNS rc 0 FOR A PARENT WHOSE OBJECT IS GONE —
        # it reads the recorded sha, it does not check existence. The old failure then
        # surfaced as `worktree add … fatal: invalid reference: <parent>` PLUS the advice
        # "recover <ref> --into <path>", i.e. the command that had just failed: a dead end
        # over content that is fully recoverable from the pin's own tree.
        raise LaneWipError(
            f"the pin {sha[:12]} is intact, but its PARENT commit {parent[:12]} is missing "
            "from the object DB, so it cannot be checked out. The pin's own tree is fine: "
            f"`git -C <a scratch worktree> read-tree {sha}` restores it, and "
            f"`git show {sha}:<path>` reads single files"
        )
    index_tree, index_problem = _pin_index_tree(sha, cwd=here)
    target = Path(args.into).resolve() if args.into else here

    if target.exists():
        # ⛔ A DIRECTORY THAT IS NOT A WORK TREE IS A STATE ERROR, NOT A GIT ERROR
        # (exit 2, not 3). A lane that recreates its old path with `mkdir` before
        # recovering reached `_target_is_dirty` and surfaced
        # `fatal: not a git repository` — work NOT restored, and a message that named
        # neither the cause nor the remedy. This is the recovery path, so it has to
        # say what to do.
        if target.is_file():
            raise LaneWipError(
                f"{target} is a file, not a worktree — move it aside and re-run; "
                "`recover` will then recreate the worktree at that path"
            )
        root_here = _worktree_root(target)
        if not root_here:
            raise LaneWipError(
                f"{target} exists but is not a git work tree, so the snapshot cannot be "
                "restored into it. Remove the directory (`rmdir` if empty, or move its "
                "contents aside) and re-run — `recover` will then recreate the worktree "
                "at that path"
            )
        # ⛔ A SUBDIRECTORY IS NOT A TARGET. `--is-inside-work-tree` is true for any
        # directory inside one, so restoring into `<worktree>/src` silently rewrote the
        # ENCLOSING root while the message named the subdirectory.
        if Path(root_here).resolve() != target:
            raise LaneWipError(
                f"{target} is a subdirectory of the worktree {root_here}; `--into` takes "
                f"the worktree ROOT. Re-run with `--into {root_here}`"
            )
        # ⛔ AN EXISTING TARGET CANNOT BE SWITCHED TO `--branch` — SAY SO. The branch was
        # consulted only on the absent-target path, so `recover <ref> --into <existing>
        # --branch <b>` exited 0 without a word while HEAD stayed on the target's own
        # branch; and since `pin` ALWAYS prints `--branch <b>`, a lane copying that
        # command believed its work was on `<b>` and its next commit landed elsewhere.
        # ⛔ `--force` GOVERNS THE DIRTY AND STALE GUARDS ONLY — NEVER THIS ONE. With the
        # clause `and not args.force`, the tool's own printed advice ("re-run with
        # --force") turned a refusal into a SILENT misroute: a clean target on another
        # branch was restored onto that branch, exit 0, with `--branch` unmentioned —
        # and the lane's next bare commit landed on a branch it never asked for.
        # Reachable by following `pin`'s printed command plus the refusal's own advice.
        if args.branch:
            current = _out("rev-parse", "--abbrev-ref", "HEAD", check=False, cwd=target)
            if current and current != args.branch:
                raise LaneWipError(
                    f"--branch {args.branch} was requested, but {target} already exists on "
                    f"{current}, and `recover` does not switch an existing worktree. "
                    "Restore into a NEW path to get a branch, or drop --branch to recover "
                    f"in place (the work lands as uncommitted changes on {current})"
                    + (" --force does not override this" if args.force else "")
                )

        # ⛔ REFUSE TO EAT LIVE WORK. The restore discards the target's uncommitted
        # state, so recovering onto a dirty worktree would destroy the very thing this
        # tool exists to save — a second loss, this time caused by the recovery.
        if _target_is_dirty(target) and not args.force:
            raise LaneWipError(
                f"{target} has uncommitted changes (ignored files count); recovering "
                f"{sha[:12]} there would DISCARD them. Pin it first "
                f"(`tools/lane_wip.py pin --path {target}`) and then re-run with --force "
                "— pinning makes the work recoverable, it does not make the target clean, "
                "and ignored files are never pinned, so --force is still what authorises "
                "overwriting them. Move those files aside instead if you want them kept"
            )
        # ⛔ A PIN RESTORES THE TREE IT WAS TAKEN ON. Restoring an OLD pin onto a branch
        # that has ADVANCED is a whole-tree revert of everything committed since — and
        # because the target is clean the dirty guard above cannot fire. Measured: a
        # lane committed a file after pinning, then `recover` deleted it from the
        # working tree and reverted another file, printing success with rc 0. A
        # following `git commit -a` would land that revert as a real commit.
        # ⛔ `--verify --quiet`, OR AN UNBORN HEAD IS REFUSED AS IF IT HELD WORK. Plain
        # `rev-parse HEAD` echoes its ARGUMENT (`HEAD`, rc 128) on an unborn branch, so the
        # value was truthy and != the pin's parent: a clean, EMPTY orphan worktree was
        # refused ("is at HEAD … would LOSE work") with `--force` as the only way out —
        # the flag that also disables the dirty guard. The unstage step below already used
        # `--verify --quiet`; the two disagreed.
        target_head = _out("rev-parse", "--verify", "--quiet", "HEAD", check=False, cwd=target)
        if parent and target_head and target_head != parent and not args.force:
            raise LaneWipError(
                f"{target} is at {target_head[:12]}, but {sha[:12]} was pinned on "
                f"{parent[:12]}. Restoring it would revert everything committed since — "
                f"{target} would LOSE work. Pass --force only if that is what you want, "
                "or recover the newest pin (`tools/lane_wip.py list`)"
            )
    else:
        # ⛔ THE STALE GUARD MUST ALSO RUN WHEN THE TARGET IS ABSENT. It lived only in
        # the `target.exists()` branch, so the shape `pin` itself prints — target gone,
        # `--branch` naming a branch that SURVIVED the removal — had no guard at all, and
        # a recovery there silently rewound that branch to the pin's parent. Checked
        # before the worktree is created, while nothing has been touched yet.
        if args.branch and parent and not args.force:
            tip = _out("rev-parse", "--verify", "--quiet", args.branch, check=False, cwd=here)
            if tip and tip != parent:
                raise LaneWipError(
                    f"branch {args.branch} is at {tip[:12]}, but {sha[:12]} was pinned on "
                    f"{parent[:12]}. Building the worktree there would MIX the pin's tree "
                    f"with {args.branch}'s newer commits. Recover the newest pin "
                    "(`tools/lane_wip.py list`), or pass --force to build the pin's tree on "
                    "top of that branch anyway (it does not move the branch)"
                )

        # ⛔ DETACH AT THE PIN'S PARENT, NEVER AT THE SNAPSHOT. Detaching at the
        # snapshot makes `HEAD == the snapshot`, so the restore is a no-op and the
        # recovered worktree reports a CLEAN status with an empty `git diff` — a lane
        # cannot tell that its work came back, and `git commit` answers "nothing to
        # commit". The pin is uncommitted work: it must land ON TOP of its parent.
        # ⛔ A TARGET INSIDE A PEER LANE'S WORKTREE MAKES A NESTED WORKTREE, and the
        # guard for that lived only in the `target.exists()` branch. Measured: recovering
        # into `<peer>/newsub` succeeded, and the peer's next `git add -A` recorded the
        # recovered work as a mode-160000 GITLINK to an arbitrary commit of this repo
        # (`warning: adding embedded git repository`) — invalid in every clone — while the
        # recovered tree sat in a directory the peer's `git clean -xdf`/reaper can delete.
        # ⛔ BUT NOT THE FLEET'S OWN LAYOUT. Every lane lives at
        # `<repo>/.worktrees/<lane>` and `.worktrees/` is normally GITIGNORED, so the
        # enclosing root is the MAIN CHECKOUT and the guard above refused the one command
        # `RECIPE` step 2 prescribes — with a remedy ("restore into <enclosing>") that
        # means restoring a lane's snapshot on top of the main checkout, an A-grade
        # outcome this guard exists to prevent. The hazard is not "inside a repo", it is
        # "inside a worktree that would RECORD the target": a path the enclosing worktree
        # IGNORES can never become a gitlink, and `git add -A` skips it. So gate on that.
        probe = target.parent
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        enclosing = _worktree_root(probe) if probe.exists() else ""
        if enclosing and Path(enclosing).resolve() != target:
            ignored = _git("check-ignore", "-q", str(target), check=False, cwd=enclosing).returncode
            if ignored != 0:
                raise LaneWipError(
                    f"--into {target} would create a NESTED worktree inside {enclosing} at "
                    "a path that worktree does NOT ignore, so its next `git add -A` "
                    "records the recovered work as a mode-160000 GITLINK to a commit of "
                    "this repo (invalid in every clone), and the recovery then sits where "
                    f"that worktree's cleanup can delete it. Restore into a path OUTSIDE "
                    f"{enclosing}, or into the main checkout's own ignored lane directory"
                )

        base = args.branch or parent or sha

        def _add(*extra: str, onto: str | None = None) -> subprocess.CompletedProcess[str]:
            # `onto` is the commit to START from, which is NOT `base` in the -b form: the
            # new branch must be created AT THE PIN'S PARENT, never at itself (that was
            # `fatal: invalid reference: <branch>` — naming a ref that does not exist yet).
            return _git("worktree", "add", *extra, str(target), onto or base, check=False, cwd=here)

        # ⛔ THE BRANCH CAN OUTLIVE ITS WORKTREE ONLY UNTIL THE REAPER TAKES IT. `pin`
        # prints a recovery command naming the branch, and `worktree add <path> <branch>`
        # then fails `fatal: invalid reference: <branch>` once it is gone (measured) —
        # the printed remedy, broken. Recreate it at the pin's parent, where it was.
        if (
            args.branch
            and _git("rev-parse", "--verify", "-q", args.branch, check=False, cwd=here).returncode
            != 0
        ):
            print(
                f"lane_wip: branch {args.branch} no longer exists; recreating it at "
                f"{(parent or sha)[:12]}",
                file=sys.stderr,
            )
            add = _add("-b", args.branch, onto=parent or sha)
        elif args.branch:
            add = _add()
        else:
            add = _add("--detach")

        if add.returncode != 0:
            msg = (add.stderr or add.stdout).strip()
            # ⛔ A DIRECTORY REMOVED BY ANYTHING OTHER THAN `worktree remove` LEAVES GIT'S
            # ADMIN RECORD BEHIND — `rm -rf`, a torn-down cell, an evacuated workspace:
            # the `cwd … (deleted)` shape this tool exists for. `worktree add` then
            # refuses the path (`is a missing but already registered worktree`), so the
            # tool's own recovery command dead-ended at git's voice while the pin was
            # fine. The target provably does not exist here, so the stale registration
            # can be pruned and the add retried.
            # ⛔ A DEAD HOLDER BLOCKS A NEW PATH, AND ITS MESSAGE IS DIFFERENT. When the
            # pin's worktree was `rm -rf`'d and the caller recovers into a NEW path with
            # `--branch`, git answers `fatal: '<branch>' is already used by worktree at
            # '<dead path>'` — not the `already registered worktree` text, which it emits
            # only when the PATH matches. So the handler below never ran, the tool raised
            # with the command that had just failed as its remedy, and `--force` did not
            # help — while the very same dead record was overridden on the same-path
            # branch. That is the shape this tool exists for (a directory removed without
            # `git worktree remove`), so it must not be a dead end.
            if "is already used by worktree at" in msg or "is already checked out at" in msg:
                holder = _branch_holder(args.branch, here) if args.branch else None
                if holder and Path(holder[0]).exists() and not args.force:
                    raise LaneWipError(
                        f"branch {args.branch} is checked out in the LIVE worktree "
                        f"{holder[0]}; two worktrees on one branch let a commit in one "
                        "rewrite the other's base. Recover detached (omit --branch) or "
                        "pass --force if you really mean to share the branch"
                    )
                # The holder is provably gone (the pre-check already refused a live one):
                # `-f` overrides exactly that stale record and nothing else.
                # ⛔ PASS THE BRANCH AS THE START POINT, NOT THE PARENT SHA. `extra` without
                # `-b` plus `onto=parent` made git DETACH, so the recovered work never
                # reached the branch — measurably so: `rev-parse --abbrev-ref HEAD` was
                # `HEAD`, and after the lane's next commit `git branch --contains HEAD` said
                # `(no branch)` — while the tool printed "created worktree … on <branch>".
                # The branch is the ordinary survivor of a worktree removal, so that is the
                # ordinary case. `onto=None` means `base = args.branch`, i.e. check it out.
                extra = ["-f"]
                onto: str | None = None
                if args.branch and not _branch_exists(args.branch, here):
                    extra += ["-b", args.branch]
                    onto = parent or sha
                add = _add(*extra, onto=onto)
                msg = (add.stderr or add.stdout).strip() if add.returncode != 0 else ""

            if "already registered worktree" in msg:
                # ⛔ NEVER `git worktree prune` HERE. `prune` has NO path scope: it is
                # REPO-WIDE, and multiple stale registrations are exactly the state this
                # tool exists for (`rm -rf`, a torn-down cell). Measured: recovering one
                # pin pruned a PEER lane's registration, deleted its admin index — whose
                # `AD` blob existed nowhere else — and left that blob unreachable, i.e.
                # gc food. `-f` overrides the one stale record we are addressing and
                # touches nothing else; it is also what git's own error recommends.
                # ⛔ `-f` OVERRIDES TWO SAFEGUARDS, NOT ONE: the stale record AND git's
                # "branch is already checked out by another working tree". Retrying with
                # `-f` because of the first silently defeated the second, producing TWO
                # worktrees on one live branch (measured) — the state where one lane's
                # commit rewrites the other's base, which `lane_stash` calls worse than
                # lost. The second condition is therefore checked explicitly.
                if args.branch:
                    holder = _branch_holder(args.branch, here)
                    if holder and Path(holder[0]).exists() and not args.force:
                        raise LaneWipError(
                            f"branch {args.branch} is checked out in the live worktree "
                            f"{holder[0]} (pid-visible, directory exists). Two worktrees on "
                            "one branch let a commit in one silently rewrite the other's "
                            "base. Recover detached (omit --branch) or pass --force if you "
                            "really mean to share the branch"
                        )
                extra = ["-f"]
                onto: str | None = None
                if args.branch and not _branch_exists(args.branch, here):
                    extra += ["-b", args.branch]
                    onto = parent or sha
                elif not args.branch:
                    extra.append("--detach")
                add = _add(*extra, onto=onto)
                msg = (add.stderr or add.stdout).strip() if add.returncode != 0 else ""
            if add.returncode != 0:
                raise GitFailed(
                    add.returncode,
                    f"could not create a worktree at {target}: {msg}. The PIN {sha[:12]} is "
                    "intact — `tools/lane_wip.py list --all` shows every pin in this repo, "
                    "and `recover <ref> --into <a path you name>` restores into that path",
                )
        # ⛔ ASSERT THE OUTCOME, DO NOT JUST PRINT IT. A silent misroute once printed
        # "on <branch>" over a DETACHED worktree; the message is not the contract.
        if args.branch:
            actually = _out("rev-parse", "--abbrev-ref", "HEAD", check=False, cwd=target)
            if actually and actually != args.branch:
                raise GitFailed(
                    3,
                    f"created {target} but it is on {actually}, not {args.branch} — the "
                    "work would never reach the branch. Re-run with an explicit --branch "
                    "or recover detached (omit --branch) and move the work yourself",
                )
        print(
            f"created worktree {target}"
            + (f" on {args.branch}" if args.branch else f" (detached at {base[:12]})")
        )

    cp = _git("read-tree", "--reset", "-u", sha, check=False, cwd=target)
    if cp.returncode != 0:
        raise GitFailed(
            cp.returncode,
            f"could not restore {sha[:12]} into {target}: {(cp.stderr or cp.stdout).strip()}",
        )
    # ⛔ THE SNAPSHOT RESTORES THE WORKING TREE, NOT A STAGED COMMIT. `read-tree` leaves
    # the snapshot staged, but a lane's lost state was mostly UNSTAGED — so put the index
    # back on its base and leave the files alone.
    #
    # ⛔ IT MUST BE `read-tree`, NEVER `reset --mixed`. `reset --mixed <commit>` also
    # moves HEAD — and therefore the checked-out BRANCH REF — to <commit>. Measured: a
    # lane pinned, work landed on the branch, the worktree was removed, and the recovery
    # command `pin` itself prints (`recover <ref> --into <path> --branch <branch>`, whose
    # target is ABSENT so the stale guard never ran) exited 0 having rewound the branch
    # from 7f202164 back to the pin's parent 43e3e5c8 — after which the landed commit was
    # reachable from NO ref and `reflog expire && gc --prune=now` destroyed it. A
    # recovery tool that destroys committed work on a success exit is the worst failure
    # this file can have. `read-tree <commit>` updates the INDEX only.
    # ⛔ THIS RESULT IS NOT OPTIONAL. A swallowed failure printed `recovered N file(s)`
    # and exited 0 with the snapshot left STAGED — the "the lane's next commit does
    # nothing unexpected" contract, silently unmet.
    # ⛔ UNSTAGE ONTO THE TARGET'S OWN HEAD, NEVER ONTO THE PIN'S PARENT. They are the
    # same commit in every non-forced shape, which is what hid the bug; under `--force`
    # past the stale guard they differ, and `read-tree <parent>` then STAGED A WHOLE-TREE
    # REVERT of everything committed since the pin — measured: `git status` showed
    # `D big.py` / `D landed.py` cached, and the lane's next bare commit (the exact action
    # this file reasons about) DELETED the pin's own 4,500-line file and a post-pin
    # commit from the branch, exit 0. Unstaging onto the target's HEAD instead leaves the
    # promised shape: the pin's tree as an UNSTAGED diff on top of that branch.
    target_head = _out("rev-parse", "--verify", "--quiet", "HEAD", check=False, cwd=target)
    if target_head:
        unstaged = _git("read-tree", target_head, check=False, cwd=target)
        what = target_head
    else:
        # An unborn HEAD has no tree; the index must start EMPTY, or every recovered file
        # is staged as an addition on a branch that has nothing.
        unstaged = _git("read-tree", "--empty", check=False, cwd=target)
        what = "the empty tree"
    if unstaged.returncode != 0:
        raise GitFailed(
            unstaged.returncode,
            f"restored {sha[:12]} into {target} but could not unstage it "
            f"(`git read-tree {what[:12]}` failed): {(unstaged.stderr or unstaged.stdout).strip()}",
        )
    staged_note = ""
    if index_tree:
        # ⛔ THE STAGED/UNSTAGED SPLIT IS PART OF THE STATE. A lane that had staged part
        # of its work recovered with an empty `diff --cached`, so its next bare
        # `git commit` — which would have committed the staged part — committed
        # nothing: a silent no-op on recovered work.
        # The target's HEAD is the pin's parent in every ordinary shape, so the plain
        # read-tree is exact. When they differ (`--force` past the stale guard) a whole
        # tree would stage a revert, so only the pin's staged DELTA is applied.
        # ⛔ `target_head != parent` — NOT `target_head and …`. On an unborn branch
        # `target_head` is "" and the else-branch wrote the WHOLE tree rooted at the pin's
        # PARENT: measured, a lane that had staged ONE file got its entire base tree staged
        # ("with the staged/unstaged split it was pinned with"), so a bare `git commit`
        # created a root commit carrying `.gitignore`, `tracked.txt` at its OLD content and
        # a deleted file. The index is empty at this point (the `--empty` unstage above), so
        # the delta is exactly the staged change.
        if target_head != parent:
            problem = _apply_staged_delta(parent, index_tree, target=target)
            if problem:
                raise GitFailed(
                    3,
                    f"restored the worktree of {sha[:12]} but could not restore its staged "
                    f"state onto {target_head[:12]}: {problem}",
                )
            staged = subprocess.CompletedProcess([], 0, "", "")
        else:
            staged = _git("read-tree", index_tree, check=False, cwd=target)
        if staged.returncode != 0:
            raise GitFailed(
                staged.returncode,
                f"restored the worktree of {sha[:12]} but could not restore its staged "
                f"state ({index_tree[:12]}): {(staged.stderr or staged.stdout).strip()}",
            )
        staged_note = " (with the staged/unstaged split it was pinned with)"
    if index_problem:
        # ⛔ THE WORKTREE CAME BACK, SO THIS IS NOT A CLEAN SUCCESS AND NOT A TOTAL
        # FAILURE. Say exactly that, and name the salvage route: the content is still
        # readable from the pin's second parent or the pin itself.
        raise GitFailed(
            3,
            f"restored the WORKTREE of {sha[:12]} into {target}, but {index_problem}. "
            "The files are back; the staged/unstaged split is NOT. Salvage the staged "
            f"content with `git -C {target} read-tree {sha}^2` (or "
            f"`git show {sha}^2:<path>`) if that object is readable there",
        )
    # ⛔ COUNTED FROM THE PIN, IN `here`, BEFORE THE RESTORE IS REPORTED. Reading it from
    # the target AFTERWARDS made a display-only failure exit 3 over a completed restore,
    # which pushes the lane into `--force` — the flag that can revert committed work.
    # ⛔ NEVER REPORT A RESTORE THE TARGET DID NOT RECEIVE. `read-tree --reset -u` honours
    # the TARGET's sparse-checkout cone, so a sparse target silently skipped files that the
    # pin holds, `git status` was clean for them, and the tool printed "recovered N
    # file(s)" from the PIN (measured). Check the FILESYSTEM, and fail loudly.
    # ⛔ `lexists`, NOT `exists`: `exists()` FOLLOWS symlinks, so a dangling symlink —
    # restored correctly by `read-tree` — read as missing and failed an intact recovery.
    missing = [rel for rel in pin_files if not os.path.lexists(target / rel)]
    if missing:
        shown = ", ".join(missing[:5]) + (" …" if len(missing) > 5 else "")
        raise GitFailed(
            3,
            f"restored {len(pin_files) - len(missing)} of {len(pin_files)} file(s) from "
            f"{sha[:12]} into {target}; {len(missing)} did NOT appear: {shown}. A sparse "
            "checkout or a skip-worktree entry can cause this — widen it "
            "(`git sparse-checkout disable`) and re-run",
        )
    print(f"recovered {len(pin_files)} file(s) into {target} from {sha[:12]}{staged_note}")
    return 0


# ------------------------------------------------------------------------ recipe


RECIPE = """\
# A lane's worktree vanished — recovery recipe (#10812)

The work is ONLY recoverable if it was pinned. `git worktree remove --force` on a
dirty tree returns 0 and the content is gone; nothing in git gets it back.

1. Find the pin for the lane whose worktree disappeared:

       tools/lane_wip.py list --all

   The lane key is `<sanitized-worktree-basename>-<sha1(path)[:8]>`, so the dead
   worktree's own name identifies its pins.

2. Recreate the worktree at its path, on its branch, and restore the snapshot:

       tools/lane_wip.py recover refs/pi-lane-wip/<key>/<n> \\
           --into /path/to/the/worktree --branch <the-branch>

   `recover` creates the worktree when the path is gone and applies the snapshot
   with `read-tree --reset -u`, which restores modifications, untracked files AND
   deletions — the working state as it was, not the commit.

3. If the pin list is EMPTY, the work is genuinely gone. The only remaining hope is
   a copy that left the worktree: a reviewer's diff snapshot (e.g.
   `/tmp/<lane>-review-diff.txt`, which is what saved lane `p1-10538`), a `git
   stash` entry, or a peer lane's checkout. Re-apply it by hand.

PREVENT IT: pin before anything that can remove the worktree — a rebase, a merge,
a long review dispatch, or a hand-off.

    tools/lane_wip.py pin --note "before the reviewer runs"
"""


def cmd_recipe(_: argparse.Namespace) -> int:
    print(RECIPE, end="")
    return 0


# --------------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lane_wip.py",
        description="Make a lane's UNCOMMITTED work survive its worktree (#10812).",
    )
    p.add_argument("--path", help="the worktree to act on (default: this checkout)")
    sub = p.add_subparsers(dest="cmd", required=True)

    # ⛔ `--path` IS ALSO DECLARED PER SUBCOMMAND, WITH `default=SUPPRESS`. The global
    # flag only parses BEFORE the subcommand, so the advice this tool prints
    # (`<tool> pin --path <target>`) was rejected with "unrecognized arguments" — a
    # remedy that does not run, printed by the one code path whose whole job is to
    # hand a human the remedy. SUPPRESS is what makes both spellings work: without
    # it the subparser's default (None) would CLOBBER a value given globally.
    def _add_path(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    pin = sub.add_parser("pin", help="pin the working state into a ref (zero-disruption)")
    pin.add_argument("--note", default="", help="why this pin exists")
    _add_path(pin)
    pin.set_defaults(func=cmd_pin)

    lst = sub.add_parser("list", help="list wip pins")
    lst.add_argument("--all", action="store_true", help="every lane, not only this one")
    _add_path(lst)
    lst.set_defaults(func=cmd_list)

    rec = sub.add_parser("recover", help="restore a pinned working state")
    rec.add_argument("ref", help="the pin ref or its commit sha")
    rec.add_argument("--into", help="the worktree path to restore into")
    rec.add_argument("--branch", help="branch to create the worktree on")
    rec.add_argument(
        "--force",
        action="store_true",
        help=(
            "overwrite a dirty target AND disable the stale-pin guard, which "
            "can revert committed work"
        ),
    )
    _add_path(rec)
    rec.set_defaults(func=cmd_recover)

    recipe = sub.add_parser("recipe", help="print the recovery recipe")
    _add_path(recipe)
    recipe.set_defaults(func=cmd_recipe)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (LaneWipError, lane_stash.LaneStashError) as exc:
        print(f"lane_wip: {exc}", file=sys.stderr)
        return 2
    except (GitFailed, lane_stash.GitFailed) as exc:
        print(f"lane_wip: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
