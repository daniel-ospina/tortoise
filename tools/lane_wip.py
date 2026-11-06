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
`tools/lane_wip.py` and its test **invokes** it — it IS registered in
`config/ci-surfaces.yml` and in `tools/ci_selection.py`'s `CORE_ALSO`, and
`tests/test_ci_selection.py` pins that), and at least two deployed
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
import contextlib
import os
import shutil
import stat
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
    stdin: bytes | None = None,
) -> subprocess.CompletedProcess:
    """`git`, with an extra env layer and `-C cwd` when a caller names one."""
    env = lane_stash._env()
    # ⛔ PARSE ENGLISH, SO ASK FOR ENGLISH. EVERY marker this module matches on
    # (`_SKIP_MARKERS`, `_is_bare_denied`, `_ABSENT_OBJECT_MARKERS`, `_NOT_A_WORKTREE`, …) is
    # git's own diagnostic PROSE, and git's `strerror` strings are gettext-translated. Nothing
    # pinned the locale, so on a host that renders "Permission denied" as
    # "Keine Berechtigung" the bare skip line matched NOTHING: measured through a shim that
    # retained real git and translated only that string, `pin` exited 0 with an EMPTY stderr
    # and held HEAD's bytes for a path inside a no-execute directory — while the disk held the
    # lane's only copy, and `git status` does NOT list that path, so `_skipped_paths` is the
    # ONLY detector for it. That is this module's forbidden verdict, rendered as a fact.
    # `LANGUAGE` overrides `LC_ALL` for gettext, so dropping `LC_ALL`'s companion matters.
    env["LC_ALL"] = "C"
    env.pop("LANGUAGE", None)
    env.update(extra_env or {})
    env.update(_IDENTITY)
    cmd = ["git", *(["-C", str(cwd)] if cwd is not None else []), *args]
    # ⛔ `text=False` AND AN EXPLICIT DECODE. `text=True` wraps stdout in a TextIOWrapper
    # with `newline=None` (universal newlines), which rewrites `\r`, `\n` and `\r\n` to
    # `\n` INSIDE the payload — and `subprocess` accepts no `newline=` argument, so it
    # cannot be repaired in place. `errors="surrogateescape"` only governs UNDECODABLE
    # bytes, so it could not help: a path byte `0x0d` became `0x0a`, which is why a lane in
    # a directory named `lane<CR>` was told "that worktree is gone, so there is nothing left
    # to pin" (no pin at all — the #10812 loss), a byte-perfect restore was reported as "1
    # did NOT appear", and the delta MINTED a phantom index entry (`caf\n.txt` for
    # `caf\r.txt`) that a bare commit then landed under a name existing nowhere on disk.
    cp0 = subprocess.run(cmd, capture_output=True, text=False, env=env, check=False, input=stdin)
    cp = subprocess.CompletedProcess(
        cp0.args,
        cp0.returncode,
        cp0.stdout.decode("utf-8", "surrogateescape"),
        cp0.stderr.decode("utf-8", "surrogateescape"),
    )
    if check and cp.returncode != 0:
        raise GitFailed(cp.returncode, (cp.stderr or cp.stdout).strip())
    return cp


_ABSENT_OBJECT_MARKERS = (
    "Not a valid object name",
    "Invalid object name",
    "bad object",
    "could not get object info",
)


def _object_present(rev: str, *, cwd: Path) -> bool:
    """True when ``rev``'s object exists. ``cat-file -e`` rc 1 = absent; anything else raises.

    ⛔ A FAILED `cat-file -e` READ IS NOT "THE OBJECT IS GONE". Collapsed, one transient
    read failure told the lane its intact pin was DAMAGED (list, RECIPE step 1), that its
    worktree tree was unreadable, or that the pin\'s PARENT was missing — three different
    falsehoods about a pin that recovered fine a second later. Same three-way contract as
    `_verify_opt`: 0 = present, 1 = absent, everything else = read failure.
    """
    cp = _git("cat-file", "-e", rev, check=False, cwd=cwd)
    if cp.returncode == 0:
        return True
    err = cp.stderr or ""
    if cp.returncode == 1 and not err.strip():
        return False
    # Measured: a genuinely MISSING object exits 128 with `fatal: Not a valid object name
    # <rev>` — it is not an rc-1 command — so the "absent" answer must be git's own
    # diagnostic, and anything else (a torn read, a shim) stays a read failure.
    if any(m in err for m in _ABSENT_OBJECT_MARKERS):
        return False
    raise GitFailed(
        cp.returncode,
        f"could not read {rev}: {(cp.stderr or cp.stdout).strip() or 'no diagnostic'}",
    )


def _inside_work_tree(path: Path, *, what: str) -> bool:
    """``rev-parse --is-inside-work-tree``, with "not a repo" separated from a FAILED read.

    ⛔ `rc != 0` IS TWO DIFFERENT ANSWERS. Outside a repository git says
    `fatal: not a git repository` (rc 128) — a real "no". A failed read (transient I/O, a
    shim, a torn-down cell) also exits non-zero, and rendering it as "not a git work tree"
    produced false statements about a live worktree at four call sites.
    """
    cp = _git("rev-parse", "--is-inside-work-tree", check=False, cwd=path)
    if cp.returncode == 0:
        return cp.stdout.strip() == "true"
    err = cp.stderr or ""
    if "not a git repository" in err or "cannot change to" in err:
        return False
    raise GitFailed(
        cp.returncode,
        f"could not decide whether {path} is a {what}: {(err or cp.stdout).strip() or 'no diagnostic'}",
    )


def _verify_opt(rev: str, *, cwd: Path) -> str:
    """``rev``'s sha, or ``""`` when it names NOTHING. A FAILED READ raises.

    ⛔ `_out(..., check=False)` collapsed "the command failed" into `""`, which is the same
    value as "this name does not resolve" — and every caller below reads `""` as a POSITIVE
    fact. Measured consequences, all with exit 0:

    * the target's `HEAD`: a failed read looked like an UNBORN HEAD, so the index was wiped
      (`read-tree --empty`) and the pin's staged DELTA was applied to an empty index — the
      round-9 staged whole-tree revert — and the lane's next bare commit DELETED `.gitignore`,
      `doomed.txt` and `tracked.txt` off the branch;
    * the pin's `^2`: a failed read looked like "this pin recorded no staged half", so the
      split was dropped with no warning and a file that was `AM` came back `??`;
    * the branch tip: a failed read skipped the STALE guard, so an old pin was built on an
      advanced branch without `--force`, removing a post-pin commit's file;
    * the pin's `^1`: a failed read looked like a foreign/damaged pin and refused an intact one.

    `--verify --quiet` answers rc 1 with NO output for a name that does not resolve — that is
    the only shape this function may treat as absent. Anything else (`rc 128`, or any stderr)
    is a read failure and must be reported: an UNKNOWN must never render as a fact.
    """
    cp = _git("rev-parse", "--verify", "--quiet", rev, check=False, cwd=cwd)
    if cp.returncode == 0:
        return cp.stdout.strip()
    if cp.returncode == 1 and not (cp.stderr or "").strip() and not cp.stdout.strip():
        return ""
    raise GitFailed(
        cp.returncode,
        f"could not read {rev}: {(cp.stderr or cp.stdout).strip() or 'no diagnostic'}",
    )


def _repo_root() -> Path:
    """The worktree root, decoded BYTE-EXACTLY.

    A lossy `replace` decode turned a worktree at a non-UTF-8
    directory (`/tmp/lane\xe9`) into a path that does not exist, and `pin` then answered
    "that worktree is gone, so there is nothing left to pin" — the false "your work is
    gone" verdict this tool documents as never giving.
    """
    cp = _git("rev-parse", "--show-toplevel", check=False)
    if cp.returncode != 0:
        err = cp.stderr or ""
        # ⛔ A FAILED READ IS NOT "NOT A WORK TREE". Outside a repository git says
        # `fatal: not a git repository`; anything else is a read failure, and rendering it
        # as a fact exited 2 with "not inside a git work tree — nothing to pin" over a lane
        # whose `git status` worked (measured). Same contract as `_worktree_root`.
        if any(m in err for m in _NOT_A_WORKTREE):
            raise LaneWipError("not inside a git work tree — nothing to pin")
        raise GitFailed(
            cp.returncode,
            f"could not read the repository root: {(err or cp.stdout).strip() or 'no diagnostic'}",
        )
    # ⛔ `removesuffix("\n")`, NOT `strip()`. A directory whose NAME ends in a space
    # (`/tmp/x/lane `) is legal, and `strip()` ate that space: `pin` then answered
    # "`/tmp/x/lane` is not a directory — that worktree is gone, so there is nothing left to
    # pin" (rc 2) for a LIVE lane, `list` said it "may have been removed", `list --all` died
    # in `for-each-ref`, and `recover --into` named a path that does not exist. No pin could
    # be created at all, so such a lane lost its work exactly as #10812 describes — and the
    # verdict it got was the one this tool documents as never giving. Only the newline git
    # appends is ours to drop; the rest is the filesystem's name.
    return Path(cp.stdout.removesuffix("\n"))


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


#: Diagnostics from `git add`/`git status` that PROVE a path was not indexed. The
#: classifier is a DECISION list, not a prefix rule: `warning:` alone is advisory (git writes
#: one for an ordinary nested repo and for CRLF conversion, over a pin that holds every path),
#: while these name content git could not read. Measured: a mode-000 FILE speaks
#: `error: unable to index file`; a mode-000 DIRECTORY speaks only
#: `warning: could not open directory` and `git add -A --ignore-errors` EXITS 0 — so a
#: prefix rule over `error:` alone missed it completely.
_SKIP_MARKERS = (
    "error:",
    "could not open",
    "cannot open",
    "unable to open",
    "unable to index",
    "could not read",
)


def _resolve_path(path: Path, *, what: str) -> Path:
    """``path.resolve()``, with a symlink loop (or an unreadable parent) as a STATE ERROR.

    ⛔ `Path.resolve()` raises `RuntimeError("Symlink loop …")` — NOT `OSError` — and an
    uncaught one exited 1 with a traceback on every verb (`pin`/`list`/`recover`/`recipe`).
    A lane path replaced by a stale symlink loop is exactly the shape `--path` is used on
    once a lane is already confused, so the tool must answer with a remedy, not a crash.
    """
    try:
        return path.resolve()
    except (RuntimeError, OSError) as exc:
        raise LaneWipError(
            f"{what} {path} cannot be resolved ({exc}) — a symlink loop or an unreadable "
            "parent directory. Replace it with a real path (or repair the symlink), then "
            "re-run"
        ) from exc


def _is_bare_denied(line: str) -> bool:
    """`git add`'s BARE ``<path>: Permission denied`` — no ``error:``/``warning:`` prefix.

    ⛔ THIS FORM WAS UNMATCHED, so a path inside a directory without EXECUTE permission was
    never counted as skipped: `git add -A --sparse --ignore-errors` writes
    ``d/f.txt: Permission denied`` and EXITS 0, `_SKIP_MARKERS` matched none of it, so
    `unreadable` stayed empty, nothing warned, and the pin held HEAD's bytes for the lane's
    only copy — exactly the silent-HEAD shape this module forbids. Measured with `chmod 0644
    d/` over a modified `d/f.txt`: stderr was that one line and `pin` printed `pinned 2
    file(s)` with an empty warning.
    """
    return line.endswith(": Permission denied") and not line.startswith(
        ("error:", "warning:", "fatal:", "hint:", "note:")
    )


def _skipped_paths(stderr: str) -> str:
    """The lines of ``stderr`` that prove content was NOT indexed (``""`` when none)."""
    lines = [ln for ln in (stderr or "").splitlines() if ln.strip()]
    return "\n".join(
        ln for ln in lines if any(m in ln for m in _SKIP_MARKERS) or _is_bare_denied(ln)
    ).strip()


def _worktree_root(path: Path) -> str:
    """The worktree ROOT containing ``path``, or ``""`` when it is not a work tree.

    ⛔ A SUBDIRECTORY IS NOT A LANE. `rev-parse --is-inside-work-tree` is true for
    any directory inside one, and the lane key is derived from the path given — so
    `pin --path <worktree>/src` minted `…/src-<digest>/n`, a DIFFERENT key from the
    worktree's own, and `list` at the root then answered "lane … has no wip pin".
    That is the false "the work is gone" verdict the RECIPE path must never give.
    """
    # ⛔ "NOT A WORK TREE" AND "THE READ FAILED" MUST NOT BE THE SAME ANSWER. `""` skipped
    # the nested-worktree guard entirely (measured: `recover --into <peer>/newsub` created a
    # nested worktree, exit 0, and the peer's next `git add -A` recorded the recovered work
    # as a mode-160000 gitlink — the harm that guard exists to prevent), and made `pin --path
    # <wt>/src` mint a pin under the SUBDIRECTORY's lane key. Same policy as
    # `_branch_holder`: an UNKNOWN fails closed.
    cp = _git("rev-parse", "--show-toplevel", check=False, cwd=path)
    if cp.returncode == 0:
        return cp.stdout.removesuffix("\n")
    err = cp.stderr or ""
    # The only shapes that genuinely mean "there is no enclosing work tree": git says so,
    # or the directory is not there at all.
    if any(m in err for m in _NOT_A_WORKTREE):
        return ""
    raise GitFailed(
        cp.returncode,
        f"could not read the worktree root of {path}: "
        f"{(err or cp.stdout).strip() or 'no diagnostic'}",
    )


_NOT_A_WORKTREE = (
    "not a git repository",
    "this operation must be run in a work tree",
    "cannot change to",
)


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
    pf = _git("rev-parse", "--path-format=absolute", "--git-path", "index", check=False, cwd=root)
    index_path = pf.stdout.strip() if pf.returncode == 0 else ""
    if not index_path:
        err = pf.stderr or ""
        # ⛔ AN OLD GIT AND A FAILED READ ARE DIFFERENT ANSWERS. `rc != 0` was rendered as
        # the version verdict, so a transient failure told the lane its git was < 2.31.
        # ⛔ "unknown option" IS THE ONLY SHAPE THAT PROVES A VERSION PROBLEM.
        if "unknown option" in err or "unrecognized argument" in err:
            index_path = ""  # the caller's message already names the version requirement
        else:
            raise GitFailed(
                pf.returncode,
                f"could not locate the index: {(err or pf.stdout).strip() or 'no diagnostic'}",
            )
    # ⛔ NO FALLBACK TO THE REAL INDEX — IT MINTS A LIE. The fallback ran
    # `write-tree` against the REAL index, which (a) TAKES `index.lock` and rewrites the
    # index a pin claims not to touch, and (b) turned "I could not read the index" into
    # the concrete EMPTY TREE, announced as "staged state is pinned too". Measured: a lane
    # whose `.git/index` was absent pinned `4b825dc6…` as its staged half, and `recover`
    # then staged the WHOLE tree as deleted — a bare `git commit` deletes the branch.
    # (c) `rev-parse --path-format` is `check=False`, so on git < 2.31 EVERY pin took this
    # path silently, re-opening the staged-half-lost-under-a-concurrent-`add` hole the
    # copy exists to close. An absent index means "not captured", which the caller already
    # reports loudly and never mistakes for clean.
    if not index_path:
        return (
            "",
            "this git has no `rev-parse --path-format` (needs >= 2.31), so the index could not be located",
        )
    if not Path(index_path).is_file():
        return "", f"{index_path} does not exist"
    with tempfile.TemporaryDirectory(prefix="lane-wip-rtree-") as td:
        copy = Path(td) / "index"
        try:
            # ⛔ AN UNREADABLE INDEX IS "NOT CAPTURED", NOT A TRACEBACK. `is_file()` stats
            # fine on a mode-000 (or container-written root-owned) index and `copy2` then
            # raised `PermissionError`: 0 refs written, the primary verb dead at exit 1,
            # instead of the loud "the STAGED state is NOT in this pin" degradation that
            # already exists for every other unreadable-index cause.
            shutil.copy2(index_path, copy)
        except OSError as exc:
            return "", f"{index_path} cannot be read ({exc.strerror or exc})"
        cp = _git("write-tree", check=False, cwd=root, extra_env={"GIT_INDEX_FILE": str(copy)})
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
    # ⛔ `--full-tree`, OR A cwd INSIDE A SUBDIRECTORY SILENTLY HIDES THE GITLINK. `ls-tree`
    # uses the cwd as an implicit pathspec prefix, so `recover --path <repo>/src` emitted NO
    # warning while the nested repo came back EMPTY — measured, the same ref and repo warning
    # from the root and silent from `src/`. This is the `--describe`/`pin_files` fix, one call
    # site further on.
    cp = _git("ls-tree", "-r", "--full-tree", "-z", tree, check=False, cwd=root)
    if cp.returncode != 0:
        raise GitFailed(
            cp.returncode,
            "could not read the pin's tree to find nested repositories: "
            f"{(cp.stderr or cp.stdout).strip() or 'no diagnostic'}",
        )
    entries = [e for e in cp.stdout.split("\0") if e]
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
    cp = _git("ls-files", "--stage", "-z", check=False, cwd=root)
    if cp.returncode != 0:
        raise GitFailed(
            cp.returncode,
            "could not read the index to find intent-to-add paths: "
            f"{(cp.stderr or cp.stdout).strip() or 'no diagnostic'}",
        )
    staged = [e for e in cp.stdout.split("\0") if e]
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
    # ⛔ `rc != 0` IS NOT "UNBORN HEAD". A FAILED read (transient I/O, a locked object
    # store, a shim) is not a fact about the lane's history, and the remedy this branch
    # printed ("commit once, then pin") is wrong and non-actionable on a repo that HAS
    # commits — while no pin at all was written. `_verify_opt` separates the two.
    head_sha = _verify_opt("HEAD", cwd=root)
    if not head_sha:
        raise LaneWipError(
            f"{root} has no HEAD commit — a lane's work cannot be described against "
            "an empty history; commit once, then pin"
        )
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
        # ⛔ BUILD THE WORKTREE VIEW FROM `HEAD`, NEVER FROM THE REAL INDEX. Starting from
        # the lane's index looked like the fix for a force-added ignored path, but the index
        # also carries `assume-unchanged`/`skip-worktree` FLAGS — and `git add -A` SKIPS
        # flagged entries — so the view went STALE for exactly those paths: measured,
        # `git update-index --assume-unchanged tracked.txt` plus a modified `tracked.txt` made
        # `pin` print "clean — nothing to pin" (no ref written at all), and with another dirty
        # file it pinned the HEAD content while the lane's only work sat on disk, then
        # `recover` reported success over the loss. (Clearing the flags on the copy does not
        # work either: measured, `git update-index --no-skip-worktree` under GIT_INDEX_FILE
        # exits 0 and clears NOTHING.)
        _git("read-tree", "HEAD", cwd=root, extra_env=env)
        # ⛔ `--ignore-errors`, OR ONE UNREADABLE FILE COSTS THE LANE ITS WHOLE PIN.
        # `git add -A` is all-or-nothing: a single mode-000 (or container-written
        # root-owned) path fails the whole add, so NO pin was created at all — while the
        # lane's own `git status`/`git commit` stayed healthy, so nothing warned it. That
        # is the #10812 outcome reached through a file the lane does not care about.
        # Measured: with `--ignore-errors` the add returns rc 0 having indexed everything
        # readable, and names the paths it skipped on stderr — which is then ANNOUNCED, by
        # the same "a non-empty stderr is a signal" rule `write-tree` uses below.
        add = _git_add(["-A"], cwd=root, env=env)
        # ⛔ `--ignore-errors` STILL EXITS NON-ZERO (git's own doc says so: "The command
        # shall still exit with non-zero status"), so rc is NOT the verdict here — it
        # means "some paths were skipped", which is exactly the case we must survive. The
        # `write-tree` below is the authoritative one: it succeeds over a scratch index
        # that holds everything readable, and fails on a broken index. Raising on rc alone
        # is what made one mode-000 file cost a lane its ENTIRE pin — 0 refs written, while
        # its `git status`/`git commit` stayed healthy, so nothing warned it: the #10812
        # outcome reached through a file the lane does not care about.
        # ⛔ ONLY `error:` LINES PROVE A PATH WAS SKIPPED. A non-empty stderr is NOT
        # evidence: git exits 0 and writes advisory `warning:`/`hint:` text for an ordinary
        # nested repo and for a CRLF-converted file, so treating "stderr non-empty" as
        # "paths were dropped" printed, in ONE run, both "some paths could NOT be added …
        # (a mode-000 or other-user-owned file does this; `chmod` it …)" and "1 nested
        # repository is recorded as a gitlink only" — the first naming a permissions cause
        # that did not exist, over a pin that held every path.
        unreadable = _skipped_paths(add.stderr or "")
        # ⛔ AN INDEX-ONLY PATH MUST STILL BE CAPTURED. `read-tree HEAD` + `add -A` honours
        # the ignore rules for anything not already tracked, so a force-added ignored path
        # (`git add -f .env.local`) never reached the worktree tree: measured, `pin` said
        # "pinned 3 file(s)" over a 4-path state and `recover` printed `recovered 3 file(s)
        # … with the staged/unstaged split` while the file was ABSENT from disk (only its
        # `^2` blob survived). The paths that need this are exactly "in the REAL index, not
        # in HEAD" — and only those that still exist on disk, so a staged deletion stays a
        # deletion.
        skipped = ""
        unverified = ""
        head_paths = set(_out_z("ls-tree", "-r", "-z", "--name-only", head_sha, cwd=root))
        # An UNREADABLE real index is a degraded state, not a failed pin: the worktree view
        # does not depend on it, and `_index_tree` already reports the staged half as NOT
        # captured (warning + the "clean" refusal). Failing here would take the whole pin
        # down over an index the lane's own `git status` may still read.
        real_paths = _git("ls-files", "-z", check=False, cwd=root)
        index_only = (
            [
                rel
                for rel in (e for e in real_paths.stdout.split("\0") if e)
                if rel not in head_paths and os.path.lexists(root / rel)
            ]
            if real_paths.returncode == 0
            else []
        )
        if real_paths.returncode != 0:
            # ⛔ "THE READ FAILED" IS NOT "THERE ARE NO INDEX-ONLY PATHS". Silent, the pin
            # omitted a force-added ignored path and still printed `pinned N file(s)` with an
            # EMPTY stderr, so `recover` later claimed the whole state while the file was
            # absent from disk. The pin is still worth taking (everything `add -A` saw is
            # real), so this is loud rather than fatal.
            unverified = (
                "the index could not be listed, so index-only paths (a force-added ignored "
                "file, an `add -N` entry) are NOT verified in this pin"
            )
        else:
            unverified = ""
        for start_at in range(0, len(index_only), 200):
            chunk = index_only[start_at : start_at + 200]
            # ⛔ ONE UNREADABLE INDEX-ONLY PATH MUST NOT COST THE LANE ITS WHOLE PIN.
            # `add -A` above already survives that (`--ignore-errors` + `_skipped_paths`);
            # this step re-introduced the failure it had just fixed: measured, one mode-000
            # force-added `.env.local` made `pin` exit 3 with NO ref written, over a lane
            # whose real work was perfectly readable.
            forced = _git_add(["-f", "--", *chunk], cwd=root, env=env)
            forced_skips = _skipped_paths(forced.stderr or "")
            # ⛔ rc IS A VERDICT HERE TOO. This call had its rc read NOWHERE, so a failure whose
            # text is not a skip marker (`fatal: Unable to create '.../index.lock'`,
            # `fatal: unable to write new index file`) was recorded as CAPTURED: measured, an
            # `AM` force-added path whose v2 edit could not be indexed pinned v1 only, printed
            # `pinned 2 file(s)` with an EMPTY stderr, and `recover` restored the file ABSENT.
            # The sibling `add -A` rule eleven lines down exists for exactly this shape.
            if forced.returncode != 0 and not forced_skips:
                raise GitFailed(
                    forced.returncode,
                    "`git add -f` for the index-only paths failed, so this pin would hold "
                    "less than the lane's work while looking complete: "
                    f"{(forced.stderr or forced.stdout).strip() or 'no diagnostic'}",
                )
            skipped += forced_skips
        # PROVEN skips (git named the path) are what the CLEAN refusal may act on; an
        # UNVERIFIED index listing only earns a warning, because it proves nothing about
        # any individual path.
        unreadable = "\n".join(p for p in (unreadable, skipped) if p).strip()
        # ⛔ rc != 0 WITH NO `error:` LINE IS A HARD FAILURE, not a partial add. git's
        # per-path skips always speak `error: …` (measured: `error: unable to index file`),
        # so anything else — an empty stderr included — must not fall through to
        # `write-tree`, where a same-as-HEAD scratch index would be reported as the worst
        # possible verdict: "nothing to pin".
        if add.returncode != 0 and not unreadable:
            raise GitFailed(
                add.returncode,
                f"`git add -A` against a scratch index failed: "
                f"{(add.stderr or add.stdout).strip() or 'no diagnostic'}",
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
    if unverified:
        print(
            f"lane_wip: WARNING — {unverified}",
            file=sys.stderr,
        )
    if unreadable:
        print(
            f"lane_wip: WARNING — some paths could NOT be added to this pin and are NOT "
            f"recoverable from it:\n{unreadable}\n"
            "  (two different causes need two different fixes: a path that is NOT READABLE "
            "needs `chmod` — a DIRECTORY needs `+x` as well as `+r`. A path that is NOT A "
            "REGULAR FILE (a FIFO, socket or device) cannot be indexed at all, so no "
            "permission change can help: replace it with a real file or remove it)",
            file=sys.stderr,
        )
    worktree_clean = tree == head_tree
    # ⛔ A SECOND VIEW IS NOT ENOUGH WHEN GIT ITSELF IS SPARSE-AWARE. Two views built by
    # the SAME `git add` agree with each other and with HEAD while the lane's edit sits on
    # disk (measured: `tree == head_tree` with `git status` reporting ` M prune/b.txt`), so
    # "clean — nothing to pin" was printed over real work and no ref was written. Ask the
    # one view that is not built by `add`: `git status`. A disagreement is a REFUSAL, never
    # a pin that holds less than the lane's work while calling itself complete.
    if unreadable and worktree_clean:
        # A partial add must never be presented as "nothing to save".
        raise LaneWipError(
            f"{root} has paths that could not be indexed (see the warning above), so the "
            "pin would hold LESS than the lane's work while looking complete — fix or remove "
            "the path named above and pin again"
        )
    # ⛔ NOT GATED ON `worktree_clean`. The check exists because two `add`-built views agree
    # while an edit sits on disk — and that is just as true when the lane ALSO has a
    # capturable edit: measured on an old git, a sparse lane with an in-cone edit AND its only
    # copy of an out-of-cone file pinned HEAD's bytes with an EMPTY stderr and exited 0, and
    # `recover` restored HEAD over the work. Gate on "the pin CLAIMS completeness" instead:
    # nothing proven skipped, and an index half captured (so the split claim is meaningful).
    # ⛔ ONE UNREADABLE PATH MUST NOT SWITCH THIS OFF EITHER. Measured: a sparse lane carrying
    # one `chmod 000` artifact printed `pinned 3 file(s)` with a warning naming only THAT file,
    # while the pin held HEAD's bytes for the out-of-cone path — `unreadable` disabled the only
    # view that would have caught it. A path whose own bytes cannot be read is announced by name
    # and never compared; every OTHER dirty path is checked. Only a missing index half skips
    # this, because then the split claim it guards is not being made.
    if index_tree:
        dirty_paths = _pin_lacks_change(root, tree, _paths_git_calls_dirty(root))
        if dirty_paths:
            shown = ", ".join(dirty_paths[:5]) + (" …" if len(dirty_paths) > 5 else "")
            raise GitFailed(
                3,
                f"`git status` reports {len(dirty_paths)} change(s) in {root} that this pin "
                f"does NOT hold ({shown}), so it would be reported as complete while missing "
                "them. A sparse checkout outside this git's `--sparse` support and a "
                "skip-worktree entry both do this",
            )
    if index_tree:
        ita = _intent_to_add(root, index_tree)
        if ita:
            shown = ", ".join(ita[:5]) + (" …" if len(ita) > 5 else "")
            print(
                f"lane_wip: WARNING — {len(ita)} intent-to-add path(s) keep their CONTENT in "
                f"this pin but lose their STAGED (intent-to-add) state: {shown}. `git "
                "write-tree` omits the entry, so `recover` restores the file as untracked "
                "(` A` becomes `??`), which makes a bare `git commit -a` skip it; run "
                "`git add <path>` (or `git rm --cached <path>`) and pin again if that "
                "matters",
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


def _add_fails_on_the_option(stderr: str) -> bool:
    """Does this `git add` failure look like "the flag does not exist on this git"?"""
    low = (stderr or "").lower()
    return any(
        marker in low
        for marker in (
            "unknown option",
            "unrecognized option",
            "does not take",
            "unknown switch",
            "usage: git add",
        )
    )


def _git_add(flags: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    """⛔ `--sparse`, OR A SPARSE WORKTREE'S OUT-OF-CONE WORK IS SILENTLY SKIPPED.

    `git add -A` is SPARSE-AWARE: outside the cone it skips the path at **rc 0 with an
    empty stderr**, so neither the rc guard nor the `error:`-line classifier can fire.
    Measured in a `sparse-checkout set --cone keep` lane: `read-tree HEAD` + `add -A` +
    `write-tree` produced a tree EQUAL to HEAD's while `git status` in the same directory
    said ` M prune/b.txt` — so `pin` printed "clean — nothing to pin", wrote NO ref, and
    the lane's only copy of those bytes stayed a file on disk with no object in the DB (the
    #10812 loss, under a false "nothing to save" verdict). With another in-cone edit it
    printed `pinned N file(s)` while the pin held HEAD's content for that path, and
    `recover` restored HEAD over the lane's edit. `--sparse` captures both.
    A git older than 2.41 has no `--sparse`; there it is absent rather than fatal, and the
    cross-check below still refuses the CLEAN verdict.
    """

    # ⛔ THE OPTIONS GO BEFORE ANY `--`. Appending them after the separator (`add -f --
    # <paths> --sparse`) made git read the two flags AS PATHSPECS: measured, the force-added
    # ignored path was silently NOT pinned (`pin` reported 3 files where the state held 4)
    # and the `error: pathspec` lines matched no skip marker, so no warning fired either.
    def _args(*opts: str) -> list[str]:
        if "--" in flags:
            cut = flags.index("--")
            return [*flags[:cut], *opts, *flags[cut:]]
        return [*flags, *opts]

    cp = _git("add", *_args("--sparse", "--ignore-errors"), check=False, cwd=cwd, extra_env=env)
    if cp.returncode != 0 and _add_fails_on_the_option(cp.stderr or ""):
        cp = _git("add", *_args("--ignore-errors"), check=False, cwd=cwd, extra_env=env)
    return cp


def _worktree_blob(root: Path, rel: str) -> tuple[str | None, bool]:
    """``(blob, read_failed)`` for a worktree path.

    ⛔ TWO DIFFERENT "NO BLOB" OUTCOMES, AND THEY MUST NOT BE CONFLATED. A path the PIN SOURCE
    cannot read at all (mode 000, other-user-owned) is a degraded state `pin` already announces
    by name — refusing everything because of it breaks a lane over one artifact it does not
    care about. A `git` READ that failed is an UNKNOWN about work that may be perfectly
    readable, and that must fail closed. Only the second sets `read_failed`.
    ⛔ IT MUST BE A `stat` PROBE, NEVER `open`. `open()` on a FIFO with no writer BLOCKS
    FOREVER: `pin` then hangs, writes NO ref and prints nothing, so any caller with a timeout
    proceeds unpinned and the lane's *readable* work is destroyed — the #10812 outcome, now
    caused by one degraded path instead of surviving it (measured: the pre-delta head pinned
    and warned, this one hung).
    ⛔ `hash-object <path>` FOLLOWS A SYMLINK, so it answers with the TARGET's bytes while the
    tree entry is mode 120000 holding the LINK TEXT — a symlink then reads as "the pin does
    not hold it" (a false refusal) or, for a dangling link, `fatal: could not open` and no
    verdict at all.
    """
    path = root / rel
    if path.is_symlink():
        try:
            target = os.readlink(os.fsencode(path))
        except OSError:
            return None, False
        cp = _git("hash-object", "--stdin", check=False, cwd=root, stdin=target)
        if cp.returncode != 0:
            return None, True
        return cp.stdout.strip() or None, False
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return None, True  # the STATE itself is unreadable: an UNKNOWN, never a pass
    if not stat.S_ISREG(mode):
        # A FIFO/socket/device has no blob to compare and git indexes none of them, so the
        # pin cannot be missing their content. Announced by `git add`'s own skip diagnostic.
        return None, False
    if not os.access(path, os.R_OK):
        # `os.access` uses REAL ids, exactly as `git add` does, so this agrees with what git
        # could read (mode-000 under a normal user -> False; as root -> True, and git indexes
        # it, so there is nothing degraded to report).
        return None, False  # the PIN SOURCE cannot read it: announced, never compared
    got = _git("hash-object", "--", rel, check=False, cwd=root)
    if got.returncode != 0:
        return None, True
    return got.stdout.strip() or None, False


def _pin_lacks_change(root: Path, tree: str, dirty: list[str]) -> list[str]:
    """Dirty paths whose CONTENT the pin does not hold (compared blob-for-blob).

    ⛔ A PATH NAME IS NOT THE WORK. In a sparse lane the skipped path is still IN `HEAD`'s
    tree, so a name-only check ("is `prune/b.txt` in the pin?") answers yes while the pin
    holds HEAD's bytes and the lane's edit is nowhere but the disk — measured, that is
    exactly how "clean — nothing to pin" survived the first version of this cross-check.
    """
    out = []
    for rel in dirty:
        on_disk = root / rel
        if not os.path.lexists(on_disk) or on_disk.is_dir():
            continue  # a deletion is recorded by absence; nothing to compare
        # ⛔ A FAILED READ IS NOT "THE PIN HOLDS IT" — BUT AN UNREADABLE PATH IS NOT A
        # COMPARISON EITHER. `continue` answered this function's question with False, so one
        # failed `hash-object`/`ls-tree` re-opened the false "clean — nothing to pin" at exit 0
        # (measured: no ref, the lane's bytes in no object). Counting it in `out`, though, made
        # the CALLER refuse over one `chmod 000` artifact the pin already warned about by name
        # — so its own bytes are announced there and this comparison simply does not
        # contribute them. Every other dirty path is still compared.
        blob, read_failed = _worktree_blob(root, rel)
        if read_failed:
            out.append(rel)  # an UNKNOWN about readable work: never a pass
            continue
        if blob is None:
            continue
        want = _git("ls-tree", tree, "--", rel, check=False, cwd=root)
        if want.returncode != 0:
            out.append(rel)
            continue
        fields = want.stdout.split()
        if len(fields) < 3:
            out.append(rel)  # not in the pin at all
            continue
        if fields[2] != blob:
            out.append(rel)
    return out


def _paths_git_calls_dirty(root: Path) -> list[str]:
    """Paths `git status` reports, for the CLEAN cross-check (⛔ never trust one view)."""
    st = _git(
        "status",
        "--porcelain",
        "-z",
        "--untracked-files=normal",
        # ⛔ `--no-renames`, OR A STAGED RENAME INVENTS A PHANTOM "DIRTY PATH". Porcelain v1
        # emits a rename's ORIGINAL path as a BARE second NUL field with no `XY ` prefix, so
        # the `entry[3:]` parser below turns `src/old.py` into `/old.py` — a path `git status`
        # never called dirty (measured: `['/old.py']` from a lane whose only change was a clean
        # `git mv`). `_pin_lacks_change` then demands the pin hold it and refuses a good pin at
        # exit 3 with the false claim "`git status` reports 1 change(s) … that this pin does NOT
        # hold". With renames split into `A`/`D` both halves stay STAGED-ONLY (`Y` is a space),
        # so they are excluded by the rule below exactly as before, and a rename that ALSO has a
        # worktree-side edit still reports `AM <new>` — the change this function must catch.
        # Same choice as `_apply_staged_delta`'s `diff-tree --no-renames`.
        "--no-renames",
        check=False,
        cwd=root,
    )
    if st.returncode != 0:
        # ⛔ "”[]" IS THE POSITIVE ANSWER "nothing is dirty". Returning it from a FAILED read
        # turned the only defence against a silent sparse skip (git < 2.41, or any other
        # invisible skip) into "clean — nothing to pin": measured, a shim failing only
        # `status` over a sparse lane's sole edit produced exit 0, no ref, and the lane's bytes
        # in NO object. Fail closed, like every other read in this file.
        raise GitFailed(
            st.returncode,
            f"could not read the state of {root} to cross-check this pin (`git status` "
            f"failed): {(st.stderr or st.stdout).strip() or 'no diagnostic'}. Without it the "
            "pin cannot prove it holds the lane's work, so nothing is written",
        )
    # ⛔ ONLY A WORKTREE-SIDE CHANGE COUNTS. `git status` also reports STAGED-ONLY entries
    # (`A  x`), which the pin captures through its index parent and `add -f`, and those are
    # not "a change this pin does not hold" — refusing on them killed the staged-only and
    # force-added-ignored paths that rounds 29-35 had just fixed.
    out = []
    for entry in st.stdout.split("\0"):
        if len(entry) > 3 and entry[1] not in " ":
            out.append(entry[3:])
    return out


def _is_main_worktree(root: Path) -> bool:
    """True when ``root`` is the repository's MAIN worktree (the hub), not a LINKED one (a lane).

    ⛔ THE DISCRIMINATOR `_lane_root` NEEDS IS WHERE THE DIRECTORY SITS, NOT WHAT IT HOLDS. An
    ignored or empty directory is answered as its own lane KEY — required for the RECREATED-lane
    shape, because `<hub>/.worktrees/<lane>` (`mkdir -p` after a `worktree remove --force`) sits
    inside the HUB and its key must stay the LANE's so `list` finds the pins. That same rule also
    swallowed every ignored or empty subdirectory of a LIVE lane (`build/`, `.venv`, a
    just-created `newpkg`), and `pin` — the verb whose entire job is protecting that lane — then
    refused with "run inside the lane" to an operator standing in it (measured; `origin/main`
    pinned the lane there). A git dir that differs from the COMMON dir is a linked worktree, and a
    lane can only ever be one of those: so a subdirectory of a linked worktree belongs to that
    lane. Content is NOT the test — a recreated lane path that already holds rescued files is
    still inside the HUB (`test_pin_refuses_a_POPULATED_recreated_lane_path`).
    """
    # `--path-format=absolute` so the two answers are comparable: `--git-dir` is relative in the
    # main worktree's subdirectories (`/abs/.git` vs `../.git` depending on the prefix) while a
    # linked worktree answers absolute paths, and comparing them unresolved would read the MAIN
    # worktree as "linked" from any subdirectory. The repo's own floor is >= 2.31, where the flag
    # exists (`--path-format` is documented in git-rev-parse(1) since 2.31).
    cp = _git(
        "rev-parse",
        "--path-format=absolute",
        "--git-dir",
        "--git-common-dir",
        check=False,
        cwd=root,
    )
    # ⛔ A FAILED READ IS NOT A VERDICT. Guessing "linked" would key the enclosing worktree and
    # pin it under a key nobody asked for; guessing "main" would refuse a live lane. Neither is
    # this file's contract, so an unreadable answer fails closed like every other read here.
    if cp.returncode != 0:
        raise GitFailed(
            cp.returncode,
            f"could not tell whether {root} is the main worktree or a linked one: "
            f"{(cp.stderr or cp.stdout).strip() or 'no diagnostic'}. The lane key is derived "
            "from that answer, and a guessed key is a pin `list` may never find",
        )
    lines = cp.stdout.splitlines()
    if len(lines) < 2:
        raise GitFailed(
            cp.returncode,
            f"could not tell whether {root} is the main worktree or a linked one: "
            f"`rev-parse --git-dir --git-common-dir` answered {len(lines)} line(s), not 2 "
            f"({cp.stdout.strip() or 'no output'})",
        )
    return lines[0] == lines[1]


def _lane_root(path: Path) -> Path:
    """``path`` normalised to the worktree it IS — but never a LANE into its hub.

    ⛔ A SUBDIRECTORY IS NOT A LANE, AND A LANE IS NOT A SUBDIRECTORY. Normalising every
    path to its enclosing worktree root fixed `pin --path <wt>/src` (a DIFFERENT key from
    the worktree's own) but broke the shape this tool exists for: after `worktree remove
    --force`, the lane recreates its own path with `mkdir -p <hub>/.worktrees/<lane>`, and
    that directory is inside the HUB's worktree — so its key became the hub's and `list`
    answered "lane <hub-key> has no wip pin" while the lane's pins sat one ref away.
    Measured end to end: the false "the work is gone" verdict, in the exact state the RECIPE
    describes. The fleet's lane directory is GITIGNORED (`.worktrees/`), so "ignored by the
    enclosing worktree, or empty (a recreated lane path)" is the discriminator — the same
    test the nested-worktree guard already uses.

    ⛔ BUT THE HUB IS THE ONLY PLACE THAT SHAPE LIVES. `_is_main_worktree` asks where the
    directory sits: inside the MAIN worktree, an ignored/empty directory keeps its own key (the
    recreated lane); inside a LINKED worktree (a live lane), the same directory is a plain
    subdirectory of that lane and keys it. Without that half, `pin` refused `<lane>/build` and a
    just-created `<lane>/newpkg` — the lane's own primary protection verb, unavailable in the
    directory the lane is standing in.
    """
    enclosing = _worktree_root(path)
    if not enclosing:
        return path.resolve()
    enclosing_path = Path(enclosing).resolve()
    if enclosing_path == path or not path.is_dir():
        return path.resolve()
    try:
        empty = not any(path.iterdir())
    except OSError:
        empty = False
    if not empty:
        ignored = _git("check-ignore", "-q", str(path), check=False, cwd=enclosing)
        if ignored.returncode == 1:
            # An ordinary subdirectory: whatever it holds, it is not its own lane.
            return enclosing_path
        if ignored.returncode != 0:
            raise GitFailed(
                ignored.returncode,
                f"could not decide whether {enclosing} ignores {path}: "
                f"{(ignored.stderr or ignored.stdout).strip() or 'no diagnostic'}",
            )
    # EMPTY, or GITIGNORED: the RECREATED lane path (inside the HUB — its own key, so `list`
    # finds the lane's pins and `pin` refuses to snapshot the hub under it) or a subdirectory of
    # a LIVE lane (a linked worktree — that lane).
    return path.resolve() if _is_main_worktree(enclosing_path) else enclosing_path


def _callers_cwd() -> Path:
    """``Path.cwd()``, or a STATE ERROR that names the recipe instead of a traceback.

    ⛔ THE DELETED-CWD CASE IS THE ONE #10812 DESCRIBES: a lane process sitting in a
    directory that was removed under it. `Path.cwd()` raises `FileNotFoundError` there, so
    `pin` — the verb that exists to protect exactly that lane — died with a traceback at
    exit 1, the code this file reserves as "never a verdict about a lane's work". The
    `--path` side already had `_resolve_path`; the cwd side had nothing.
    """
    try:
        return Path.cwd().resolve()
    except OSError as exc:
        raise LaneWipError(
            f"the current directory no longer exists ({exc}) — this process is sitting in "
            "a lane whose directory was removed, which is the state `recipe` describes. Run "
            "`tools/lane_wip.py list --all` from a surviving directory (or pass "
            "`--path <the lane's path>`) to see what is still recoverable"
        ) from exc


def cmd_pin(args: argparse.Namespace) -> int:
    # ⛔ WITHOUT `--path` THE KEY COMES FROM THE CALLER'S CWD, NOT FROM THE REPO ROOT. A lane
    # runs `list` with its cwd inside its own (possibly recreated) directory; `_repo_root()`
    # answered the HUB, so the lane key became the hub's and `list` printed "lane hub-<key>
    # has no wip pin" while the lane's pins sat one ref away (measured).
    root = _resolve_path(Path(args.path), what="--path") if args.path else _callers_cwd()
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
    root = _lane_root(root)
    key = lane_stash.lane_key(root)
    # ⛔ A NON-REPO DIRECTORY IS A STATE ERROR, NOT A GIT FAILURE (exit 2, not 3).
    # `is_dir()` alone let `--path /tmp` reach a `rev-parse` and surface git's own
    # `fatal: not a git repository` with rc 3 — a message that names neither the
    # caller's mistake nor the remedy.
    if not _inside_work_tree(root, what="git work tree"):
        raise LaneWipError(f"{root} is not a git work tree — nothing to pin there")
    # ⛔ A PIN SOURCE MUST BE A WORKTREE ROOT, NOT A DIRECTORY INSIDE ONE. `_lane_root`
    # answers an IGNORED/EMPTY directory as its own lane KEY **when it sits inside the MAIN
    # worktree** — the RECREATED-lane shape, so `list` finds the lane's pins — and
    # `--is-inside-work-tree` is TRUE for it because it sits inside the HUB — so `pin
    # --path <recreated lane>` snapshotted the HUB's working state under the LANE's key
    # (measured: the pin held `.gitignore`/`hub_new.py`, and the printed remedy named
    # `--branch main`), or said "clean — nothing to pin" over a hub that had nothing to
    # save — the false "your work is safe" verdict, in the exact state the RECIPE describes.
    # Inside a LINKED worktree `_lane_root` answers the LANE instead, so a lane's own
    # subdirectory never reaches this refusal.
    at_root = _worktree_root(root)
    if not at_root or Path(at_root).resolve() != root:
        # ⛔ THE REMEDY MUST NAME A WORKTREE THE CALLER CAN ACTUALLY PIN. `_lane_root` keeps an
        # ignored/empty directory's OWN key only inside the MAIN worktree, so this refusal is
        # also reachable from the HUB — `<hub>/build`, `<hub>/.venv`, a just-created
        # `<hub>/newpkg` — where "run inside the lane" named a lane that does not exist
        # (measured: `origin/main` pinned the hub from `<hub>/build`, and on this branch the
        # only thing telling that operator where to go instead is this message).
        #
        # ⛔ AND A LANE'S OWN SUBDIRECTORY NEVER REACHES THIS REFUSAL AT ALL: `_lane_root`
        # answers a LINKED worktree's subdirectory as that LANE (its final
        # `_is_main_worktree` return), so `pin` from `<lane>/build` returns rc 0 above and
        # never gets here. So the MAIN arm is the one that fires in practice, and the `else` is
        # the DEFENSIVE branch (`not at_root`, or a future `_lane_root` shape) — not a second
        # live remedy. (The predicate is named, not cited by line: a line number here would rot
        # on the next insertion above it.) The `_is_main_worktree` call repeats the answer
        # `_lane_root` already computed; it is kept rather than threaded through because this
        # path exits immediately (rc 2, or 3 on an unreadable answer), so a second `rev-parse`
        # costs nothing and keeps `_lane_root`'s signature honest.
        if at_root and _is_main_worktree(Path(at_root).resolve()):
            whose = f"the MAIN worktree {at_root}"
            label = "a key of its own"
            remedy = (
                "A pin must be taken at the worktree root: run `tools/lane_wip.py pin` from "
                f"{at_root} (or `--path {at_root}`). If what you are looking for is a LANE "
                "whose worktree was removed and `mkdir`'d back under `.worktrees/`, "
                "`tools/lane_wip.py recipe` says what is still recoverable"
            )
        else:
            whose = at_root or "no repository"
            label = "this lane's key"
            remedy = (
                "A pin must be taken in the lane's own worktree before anything removes it "
                "(`tools/lane_wip.py pin` with no `--path`, run inside the lane); if the "
                "worktree is already gone, `tools/lane_wip.py recipe` says what is still "
                "recoverable"
            )
        raise LaneWipError(
            f"{root} is not a worktree root — it is a directory inside {whose}, so a pin "
            f"taken here would snapshot THAT checkout and label it with {label}. {remedy}"
        )
    # `check=False`: an unborn HEAD (a fresh `git init` with no commit) is reported by
    # `snapshot` below, which owns that message.
    branch = _current_branch(root) or "HEAD"
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
    # ⛔ THE REMEDY MUST NAME THE REPOSITORY THE PIN LIVES IN. `pin --path <a worktree in
    # another repo>` is a supported, TESTED shape (`test_pin_writes_the_ref_into_the_path_repo_not_the_caller`)
    # and it writes the ref into THAT repo — but the printed line named neither that repo nor
    # `--path`, so the command it printed assumed the CALLER's repo. Measured (caller in
    # repoA, pin taken into repoB's lane): `pin` rc 0, then the printed command run verbatim
    # -> rc 2 "'<ref>' does not name a commit in this repository", and the `list --all` it
    # advises answered "no lane has any wip pin" at rc 0 from repoA while the pin sat one ref
    # away — the false "the work is gone" verdict this module documents it must never give,
    # produced by its own primary verb.
    try:
        caller_root: Path | None = _repo_root()
    except (LaneWipError, GitFailed):
        caller_root = None  # outside a repo: certainly not the pin's repo
    if caller_root is not None and caller_root == root:
        print(f"recover with: tools/lane_wip.py recover {ref} --into <path>{handle}")
    else:
        # `--path <root>` makes `recover` read the ref from the repository that holds it,
        # and the note says where to run it when `root` itself is the destroyed lane.
        print(f"recover with: tools/lane_wip.py recover {ref} --path {root} --into <path>{handle}")
        print(
            f"  (this pin lives in {root} — the ref is readable from any worktree of THAT "
            "repository, so run it there (`list --all` too); a bare `recover` from another "
            "repository cannot see it)"
        )
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
                # ⛔ A DAMAGED PIN MUST NOT TAKE `list` DOWN, which is RECIPE step 1: one
                # ref with an out-of-range committer timestamp raised an uncaught
                # `ValueError: year 3170843 is out of range` out of this very fallback (the
                # one that exists to triage damaged pins), exiting 1 — a code the module
                # reserves as "never a verdict about a lane's work" — and hiding EVERY pin,
                # intact ones included. `when` stays empty; the row is already DAMAGED.
                try:
                    when = datetime.fromtimestamp(int(parts[1]), tz=UTC).isoformat(
                        timespec="seconds"
                    )
                except (ValueError, OverflowError, OSError):
                    when = ""
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
    try:
        # ⛔ A FAILED READ IS NOT "DANGLING". Its own docstring forbids ever rendering
        # "holds no work" wrongly — that is the one conclusion that makes a lane stop
        # looking — and `_out(check=False)` produced it for a read that merely failed.
        sha = _verify_opt(ref, cwd=cwd)
    except GitFailed as exc:
        why = str(exc).split(": ", 1)[-1]
        return {
            "ref": ref,
            "sha": "",
            "when": "",
            "note": f"{ref} — UNKNOWN: {why}",
            "files": 0,
            "damaged": True,
        }
    row = {"ref": ref, "sha": sha, "when": "", "note": "", "files": 0, "damaged": False}
    if not sha:
        row["damaged"] = True
        row["note"] = f"{ref} — DANGLING: the ref names no object; it holds no work"
        return row
    if not _object_present(sha, cwd=cwd):
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
    # deadbeef"). Keeping an `index-tree ` entry would therefore buy no safety while
    # asserting a strip that cannot fire. So there is one entry, and the last-line
    # case is pinned for BOTH former words by
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
    # ⛔ `--full-tree`, OR A cwd INSIDE A SUBDIRECTORY SILENTLY LIMITS THE LISTING. `ls-tree`
    # (unlike `ls-files`) uses the cwd as an implicit pathspec prefix, so `list --path
    # <hub>/.worktrees/<lane>` reported `0 file(s)` for a pin holding four (measured) —
    # "this pin holds nothing" is the one conclusion this function forbids rendering wrongly.
    listing = _git("ls-tree", "-r", "--full-tree", "--name-only", sha, check=False, cwd=cwd)
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
    # ⛔ WITHOUT `--path` THE KEY COMES FROM THE CALLER'S CWD, NOT FROM THE REPO ROOT. A lane
    # runs `list` with its cwd inside its own (possibly recreated) directory; `_repo_root()`
    # answered the HUB, so the lane key became the hub's and `list` printed "lane hub-<key>
    # has no wip pin" while the lane's pins sat one ref away (measured). RECIPE step 1 says
    # `list --all`, which is why this stayed hidden.
    root = _resolve_path(Path(args.path), what="--path") if args.path else _callers_cwd()
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
        root = _repo_root()
    keyed = _lane_root(root)
    # ⛔ THE KEY COMES FROM THE PATH; THE REFS COME FROM A REPOSITORY. A lane that recreated
    # its own (gitignored) path with `mkdir -p` is a KEY, not a repo — and `for-each-ref`
    # there answered `fatal: not a git repository`, i.e. "the registry is unreadable" over a
    # lane whose pins were one ref away. That is the state the RECIPE describes.
    if _inside_work_tree(keyed, what="repository"):
        root = keyed
    else:
        root = _repo_root()
        print(
            f"lane_wip: note — {keyed} is not a repository, so the pins are read from "
            f"{root}; the LANE KEY still comes from {keyed}",
            file=sys.stderr,
        )
    # ⛔ THE TWO VERBS MUST NOT DISAGREE ABOUT ONE STATE. `_lane_root` answers an
    # ignored/empty directory as its own key only inside the MAIN worktree, and keys the
    # ENCLOSING lane when the directory is a subdirectory of a LINKED one — `pin` and `list`
    # share that rule. Read BOTH keys anyway: the shapes `_lane_root` still answers as
    # own-key are exactly the ones where a ref may be filed under either key (a recreated
    # lane path inside the hub), and RECIPE step 3 reads an empty pin list as "the work is
    # genuinely gone", so a single-key read that misses is a false verdict, not a miss.
    keys = [keyed]
    enclosing = _worktree_root(keyed)
    if enclosing:
        enc = Path(enclosing).resolve()
        if enc not in {Path(keyed).resolve(), Path(root).resolve()}:
            keys.append(enc)
    patterns = [f"{WIP_NS}/"] if args.all else [f"{wip_ns(lane_stash.lane_key(k))}/" for k in keys]
    refs = []
    for pattern in patterns:
        cp = _git("for-each-ref", "--format=%(refname)", pattern, check=False, cwd=root)
        if cp.returncode != 0:
            raise GitFailed(
                cp.returncode,
                "could not read the wip refs (`git for-each-ref` failed): "
                f"{(cp.stderr or cp.stdout).strip() or 'no output'}",
            )
        refs += [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
    refs = list(dict.fromkeys(refs))
    if not refs:
        # ⛔ NAME THE REPOSITORY THE ANSWER CAME FROM. `root` can be a DIFFERENT repo than the
        # caller's (`pin --path <other repo>` writes the ref THERE), so an unqualified "no pin"
        # is a SCOPED empty list rendered as the unscoped fact "the work is gone" — and RECIPE
        # step 3 advises exactly that conclusion. Measured: `list --all` from repoA answered
        # "no lane has any wip pin" at exit 0 while the pin sat one ref away in repoB, and the
        # operator who follows the recipe stops looking.
        scope = (
            f"no lane has any wip pin in {root}"
            if args.all
            else (
                f"lane {lane_stash.lane_key(keyed)} has no wip pin in {root} — "
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
    # ⛔ A FAILED READ IS NOT "THE PIN DOES NOT EXIST". `check=False` collapsed both into
    # one message, so `recover <ref> --path /tmp` — or the documented "the lane recreated its
    # path with `mkdir`" state, which is NOT a repository — told a lane whose worktree was
    # already gone the one thing this file forbids saying wrongly: that its work is gone.
    # Measured: the same ref recovered fine from the same repo a moment later.
    if not _inside_work_tree(cwd, what="git work tree"):
        raise LaneWipError(
            f"{cwd} is not a git work tree, so the pins cannot be read from it. Point "
            "`--path` at the repository (or omit it) — the pin ref lives in the repo, not "
            "in the worktree that was removed"
        )
    sha = _verify_opt(f"{target}^{{commit}}", cwd=cwd)
    if not sha:
        raise LaneWipError(
            f"{target!r} does not name a commit in this repository — "
            "`tools/lane_wip.py list --all` shows every pin"
        )
    return sha


def _target_is_dirty(path: Path) -> tuple[bool, list[str]]:
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
        return True, []

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
    sparse_paths = []
    for entry in flags.stdout.split("\0"):
        if not entry or " " not in entry:
            continue
        tag, rel = entry.split(" ", 1)
        # ⛔ SKIP-WORKTREE IS NOT HIDDEN LOCAL CONTENT — IT IS A SPARSE CHECKOUT. Both
        # flags were refused as "would OVERWRITE them", but `S` entries ARE what
        # `git sparse-checkout set --cone` writes for every out-of-cone path, and
        # `read-tree --reset -u` SKIPS them rather than overwriting. Measured over a CLEAN
        # cone target: the refusal fired, and the flag its own message told the caller to
        # clear (`--no-assume-unchanged`) did NOT clear it (`--no-skip-worktree` did) — so
        # a sparse lane could not restore into its own worktree at all, for a loss that
        # cannot happen. Carry them out and verify their CONTENT after the restore instead.
        if tag.islower():
            flagged.append(rel)
        elif tag in ("S", "s"):
            sparse_paths.append(rel)
    if flagged:
        shown = ", ".join(flagged[:5]) + (" …" if len(flagged) > 5 else "")
        raise GitFailed(
            3,
            f"{path} holds {len(flagged)} assume-unchanged/skip-worktree path(s) whose "
            f"local content `git status` cannot see ({shown}); restoring here would "
            "OVERWRITE them. Clear the flag (`git update-index --no-assume-unchanged "
            "<path>`) or restore into a new path",
        )
    return False, sparse_paths


def _refuse_unless_holder_usable(holder: tuple[str, str] | None, args: argparse.Namespace) -> None:
    """Refuse when a worktree holding this branch is LIVE, or when that is UNKNOWN.

    ⛔ "THE RECORDED PATH IS MISSING" IS NOT "THE HOLDER IS DEAD". A worktree moved with
    plain `mv` keeps its OLD path registered, so a LIVE holder read as provably gone and
    `-f` put TWO live worktrees on one branch: measured, `recover --branch shared --into
    <new>` exited 0 without `--force`, and the moved-but-live worktree then reported the
    other lane's landed commit as a STAGED DELETION — its next bare commit reverted
    reviewed work. Git cannot tell the two states apart (both records read `prunable`), so
    the answer is a refusal that names BOTH and lets the caller assert which with --force.
    """
    if not holder or args.force:
        return
    path = holder[0]
    if Path(path).exists():
        raise LaneWipError(
            f"branch {args.branch} is checked out in the live worktree {path}; two worktrees "
            "on one branch let a commit in one silently rewrite the other's base. Recover "
            "detached (omit --branch) or pass --force if you really mean to share the branch"
        )
    raise LaneWipError(
        f"branch {args.branch} is registered to the worktree {path}, which no longer exists. "
        "That is EITHER a dead record (the directory was removed — the case this tool exists "
        "for) OR a LIVE worktree that was moved with `mv`, which keeps the old path "
        "registered. Two worktrees on one branch let a commit in one silently rewrite the "
        "other's base. Check `git worktree list`, then re-run with --force if that holder is "
        "really gone (or stop it first if it is not)"
    )


def _branch_holder(name: str, cwd: Path) -> tuple[str, str] | None:
    """``(worktree path, branch)`` for the worktree that has ``name`` checked out."""
    ref = name if name.startswith("refs/") else f"refs/heads/{name}"
    # ⛔ `-z`, NOT `splitlines()`. Porcelain v1 prints paths UNQUOTED, so a holder whose
    # path contains a newline was truncated to its first line; its `Path(...).exists()` was
    # then False and the code concluded the holder was "provably gone" - the dead-record
    # path that `-f` is allowed to override. Measured: with one LIVE holder at
    # `../lane\nnl`, `recover --branch shared --into <new>` returned 0 and printed "created
    # worktree ... on shared", leaving TWO live worktrees on one branch - the state the
    # guard exists to prevent. `-z` fields are NUL-separated and an EMPTY field ends a
    # record, so the path is carried verbatim.
    cp = _git("worktree", "list", "--porcelain", "-z", check=False, cwd=cwd)
    if cp.returncode != 0:
        # ⛔ FAIL CLOSED. `None` is this function's "no worktree has that branch", and both
        # call sites read it as PROVABLY GONE and fall through to `worktree add -f` — the
        # flag that defeats git's own "`<branch>` is already checked out" protection. So a
        # failed listing used to be indistinguishable from "no holder": measured with a shim
        # that fails only `worktree list`, `recover --branch <shared> --into <new>` returned 0
        # and printed "created worktree … on shared" while a LIVE holder at `laneA` stayed —
        # TWO live worktrees on one branch, where a commit in either rewinds the other's
        # base. `lane_stash._live_lane_keys` reads the same command and already refuses to
        # treat a failed read as an empty namespace; this is the same policy.
        raise LaneWipError(
            "could not read `git worktree list`, so a LIVE holder of "
            f"{name} cannot be ruled out; two worktrees on one branch let a commit in one "
            "silently rewrite the other's base. Re-run once the listing works"
        )
    path = ""
    for field in cp.stdout.split("\0"):
        if field.startswith("worktree "):
            path = field[len("worktree ") :]
        elif field == f"branch {ref}":
            return path, ref
    return None


def _current_branch(cwd: Path) -> str:
    """The branch ``cwd`` is ON, spelled `refs/heads/…`-less and NEVER shortened.

    ⛔ `rev-parse --abbrev-ref HEAD` / `symbolic-ref --short` SHORTEN a name that is
    ambiguous with another ref: with a tag called `lane`, git answers `heads/lane`. Four
    sites here named the branch with that form and then re-read it as a BARE name, which
    git resolves in ref-priority order — `refs/tags/<n>` BEFORE `refs/heads/<n>` — so the
    guard ended up judging the TAG while `worktree add` and `_branch_exists` (both of which
    already qualify with `refs/heads/`) acted on the BRANCH. Measured both directions: a
    perfectly safe recovery refused because the TAG was older, and a genuinely advanced
    branch passed because the TAG still pointed at the pin's parent. Reading the symbolic
    ref and stripping the known prefix cannot be shortened by anything.
    """
    # ⛔ "DETACHED" AND "THE READ FAILED" MUST NOT BE THE SAME ANSWER. `symbolic-ref
    # --quiet` answers rc 1 with NO output for a detached HEAD; anything else is a read
    # failure. Collapsed, a failed read was rendered as the fact "detached": `pin` recorded
    # `branch HEAD` and PRINTED a `recover … --into <path>` remedy with no `--branch`, so
    # the lane following the tool's own command got a worktree detached at the pin's parent
    # and its next commit landed on no branch at all (measured, rc 0).
    cp = _git("symbolic-ref", "--quiet", "HEAD", check=False, cwd=cwd)
    if cp.returncode == 1 and not (cp.stdout or "").strip() and not (cp.stderr or "").strip():
        return ""
    if cp.returncode != 0:
        raise GitFailed(
            cp.returncode,
            f"could not read the branch of {cwd}: "
            f"{(cp.stderr or cp.stdout).strip() or 'no diagnostic'}",
        )
    ref = cp.stdout.strip()
    return ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ""


def _branch_ref(name: str) -> str:
    """`refs/heads/<name>`, so a comparison can never land on a tag of the same name."""
    return name if name.startswith("refs/") else f"refs/heads/{name}"


def _branch_exists(name: str, cwd: Path) -> bool:
    """True when ``name`` resolves to a ref (a branch the reaper has not taken)."""
    ref = name if name.startswith("refs/") else f"refs/heads/{name}"
    cp = _git("show-ref", "--verify", "--quiet", ref, check=False, cwd=cwd)
    if cp.returncode == 0:
        return True
    if cp.returncode == 1:
        return False
    raise GitFailed(
        cp.returncode,
        f"could not read whether {ref} exists: "
        f"{(cp.stderr or cp.stdout).strip() or 'no diagnostic'}",
    )


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
    return _verify_opt(f"{sha}^1", cwd=cwd)


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
    second_sha = _verify_opt(f"{sha}^2", cwd=cwd)
    if not second_sha:
        return "", ""
    # ⛔ A FAILED READ IS NOT "THE TREE IS UNREADABLE". Collapsed, a transient read failure
    # was reported as pin DAMAGE — and the promised staged split was skipped while `recover`
    # printed a salvage command that then proved the pin was never damaged (measured).
    tree = _verify_opt(f"{second_sha}^{{tree}}", cwd=cwd)
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
            # ⛔ `--`, OR A PATH BEGINNING WITH `-` BECOMES A SWITCH. Every other path
            # handed to git here is absolute or prefixed by its mode; this one is bare, so
            # a staged deletion of `-dash.txt` made `update-index` answer `unknown switch
            # 'd'` and the whole staged half was reported as unrestorable (exit 3) over an
            # otherwise complete recovery.
            cp = _git("update-index", "--force-remove", "--", path, check=False, cwd=target)
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
    # ⛔ A DESTROYED `--path` IS THE STATE THIS TOOL EXISTS FOR. Every `rev-parse` ran with
    # `-C <dead>`, so the ref could not resolve and the caller was told the PIN does not
    # exist — while the same ref resolved fine in its own repo. `cmd_list` was already
    # fixed for exactly this; `cmd_recover` was not, though `--path` is declared for every
    # subcommand. Fall back to this repo and SAY SO, rather than blaming the ref.
    here = _resolve_path(Path(args.path), what="--path") if args.path else _repo_root()
    asked = here
    if args.path and not here.is_dir():
        print(
            f"lane_wip: note — {here} does not exist, so the pins are read from this "
            "repository instead; `--into` still decides where the work is restored",
            file=sys.stderr,
        )
        here = _repo_root()
    if getattr(args, "branch", None) == "HEAD":
        # `--branch HEAD` is what an older pin printed for a detached pin; treat it as
        # "no branch" rather than comparing the CALLER's HEAD to the pin's parent.
        args.branch = None
    # ⛔ NORMALISE A QUALIFIED NAME BEFORE ANY GUARD SEES IT. `_branch_ref`/`_branch_exists`
    # accept both spellings, so every guard resolved `refs/heads/x` AS the branch — but the
    # raw string was then handed to `worktree add`, and `_current_branch` (which strips ONE
    # `refs/heads/`) compared equal to it, so even the post-add misroute assertion could not
    # fire. Measured: `--branch refs/heads/newbranch` recreated the branch and created the
    # worktree on **refs/heads/refs/heads/newbranch** at exit 0 — a branch no tool will ever
    # look at — and with the branch still alive it DETACHED, failed the assertion, and left
    # the clean worktree occupying the path so the correctly-spelled retry was refused. The
    # qualified spelling is not exotic: `git worktree list --porcelain` and
    # `git for-each-ref` — the commands this tool itself reads — print it that way.
    if getattr(args, "branch", None):
        given = args.branch
        if given.startswith("refs/heads/"):
            args.branch = given[len("refs/heads/") :]
        elif given.startswith("refs/"):
            raise LaneWipError(
                f"--branch {given} names a non-branch ref; `recover` only ever checks out a "
                "BRANCH. Pass the branch name alone (e.g. `--branch my-branch`)"
            )
    sha = _resolve_commit(args.ref, cwd=here)
    if not _object_present(f"{sha}^{{tree}}", cwd=here):
        # ⛔ "NO RECOVERABLE CONTENT" IS FALSE WHEN THE SECOND PARENT SURVIVES. A pin
        # whose worktree tree object was lost but whose staged half is readable still
        # holds the only copy of an `AD` file; the old message sent the lane to a dead
        # end in `recipe`. Name the salvage command instead.
        salvage = _verify_opt(f"{sha}^2^{{tree}}", cwd=here)
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
    # ⛔ `--full-tree`, OR `--path <subdir>` COMPARES THE RESTORE AGAINST THE WRONG NAMES.
    # `ls-tree` uses the cwd as an implicit pathspec prefix AND REBASES what it prints
    # (measured: from `src/`, `ls-tree -r --name-only <sha>` returns `a.txt`, not
    # `src/a.txt`), so every path read as not-restored — exit 3, "restored 0 of 1 file(s)",
    # over a byte-complete restore, with a "sparse checkout" cause and two remedies that
    # cannot help. `_describe` carries this same fix one function away; so does the
    # disaster path now.
    pin_files = [
        p for p in _out_z("ls-tree", "-r", "--full-tree", "-z", "--name-only", sha, cwd=here)
    ]
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
    if not _object_present(f"{parent}^{{commit}}", cwd=here):
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
    # ⛔ `--into` IS NOT the only thing that decides the target: `--path` is documented as
    # "the worktree to act on", so `recover <ref> --path <the destroyed lane>` is the mirror
    # of `pin --path <wt>` and must recreate THAT worktree. Defaulting the target to the
    # fallback root instead restored the lane's uncommitted work into the CALLER's checkout
    # - exit 0, and on the ordinary fleet shape (main checkout clean and at the pin's
    # parent) every guard passed, so the next bare `git commit` put a lane's work on `main`.
    target = _resolve_path(Path(args.into), what="--into") if args.into else asked

    # ⛔ AN EMPTY DIRECTORY AT THE LANE'S PATH IS THE LANE'S OWN RECREATED PATH, NOT A
    # SUBDIRECTORY TARGET. Measured in the fleet layout: after `worktree remove --force`,
    # a lane that had merely `mkdir -p`-ed its old path was refused as "a subdirectory of
    # the worktree <hub>", and the remedy it printed was `--into <hub>` — restoring the
    # lane's uncommitted work into the MAIN CHECKOUT at exit 0, where a bare `git commit`
    # would land it. The ABSENT-target path already allowed that exact path (gitignored, so
    # no gitlink hazard), so the SAME command succeeded or failed purely on whether the
    # directory had survived. `rmdir` only ever removes an EMPTY directory, so nothing the
    # lane still holds can be lost by this, and the create path below then recreates the
    # worktree where it belongs.
    if target.exists() and target.is_dir():
        enclosing = _worktree_root(target)
        try:
            empty = not any(target.iterdir())
        except OSError as exc:
            # ⛔ AN UNREADABLE DIRECTORY IS A REFUSAL, NOT A TRACEBACK. An uncaught
            # `PermissionError` exited 1 — the code this module reserves for "never a
            # verdict about a lane's work" — on the RECOVERY path, the one whose whole job
            # is to give the lane a remedy.
            raise LaneWipError(
                f"{target} cannot be read ({exc.strerror or exc}), so it is not safe to "
                "restore into it. Fix the permissions, or recover into a new path "
                "(`--into <a path you name>`)"
            ) from exc
        # The empty directory is dropped AFTER every guard has had its say: `rmdir` used to
        # run FIRST, so a REFUSAL could already have deleted the caller's directory, and an
        # unwrapped `PermissionError` (sticky-bit `/tmp` parent, read-only mount) exited 1
        # with a traceback on the recovery path.
        drop_empty = bool(enclosing and Path(enclosing).resolve() != target and empty)
    else:
        drop_empty = False

    # `drop_empty` means "the target is effectively ABSENT": an empty recreated lane
    # directory takes the create path, where the nested/branch/stale guards still run
    # before anything is removed.
    sparse_paths: list[str] = []
    if target.exists() and not drop_empty:
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
            # ⛔ NEVER OFFER `--into <root_here>` AS THE REMEDY. Following it restores the
            # lane's snapshot into the ENCLOSING checkout — the A-grade outcome this guard
            # exists to prevent. Name the two things that actually work instead.
            raise LaneWipError(
                f"{target} is a populated subdirectory of the worktree {root_here}, and "
                "`--into` takes a worktree ROOT. Empty it if it is your lane directory "
                f"(`rm -rf {target}` after checking nothing there is wanted), or restore "
                f"into a path OUTSIDE {root_here}"
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
            # ⛔ `--abbrev-ref HEAD` ANSWERS THE LITERAL STRING `HEAD` ON AN UNBORN BRANCH,
            # so a target already on the requested (orphan) branch read as being on "HEAD"
            # and was refused with a statement that is simply false. `symbolic-ref` gives the
            # branch the target is actually ON, born or not.
            current = _current_branch(target)
            if not current:
                # DETACHED: `HEAD` is != any requested branch, so the refusal below still
                # fires. Do NOT normalise it to "" — that would let a detached target
                # through with `--branch` set, restoring onto a branch the caller never
                # gets and making its next bare commit land there (round 8).
                current = "HEAD"
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
        target_dirty, sparse_paths = _target_is_dirty(target)
        if sparse_paths:
            # ⛔ NAME THE REAL REMEDY BEFORE git's OWN REFUSAL SPEAKS. A sparse cone writes
            # `S` for every out-of-cone path, and `read-tree --reset -u` then answers
            # `error: Entry 'x' not uptodate. Cannot merge.` — truthful, but it names no
            # cause and no cure, and the apply step of the restore is where the lane sees it.
            print(
                f"lane_wip: note — {target} holds {len(sparse_paths)} skip-worktree "
                "path(s) (a sparse checkout): `git read-tree` skips or refuses them, so "
                "either widen the checkout first (`git sparse-checkout disable` in the "
                "target) or restore into a fresh path",
                file=sys.stderr,
            )
        if target_dirty and not args.force:
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
        target_head = _verify_opt("HEAD", cwd=target)
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
            # ⛔ `refs/heads/<name>`, NOT the bare name: a tag of the same name wins git's
            # ref-priority order, so the guard judged the TAG and either refused a safe
            # recovery or passed an advanced branch.
            tip = _verify_opt(_branch_ref(args.branch), cwd=here)
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
            ignored_cp = _git("check-ignore", "-q", str(target), check=False, cwd=enclosing)
            if ignored_cp.returncode not in (0, 1):
                # ⛔ "NOT IGNORED" AND "THE READ FAILED" ARE DIFFERENT ANSWERS. Merged, a failed
                # read refused the FLEET'S OWN recovery path with a remedy that named the very
                # path it had just refused (measured).
                raise GitFailed(
                    ignored_cp.returncode,
                    f"could not decide whether {enclosing} ignores {target}: "
                    f"{(ignored_cp.stderr or ignored_cp.stdout).strip() or 'no diagnostic'}",
                )
            ignored = ignored_cp.returncode
            if ignored != 0:
                raise LaneWipError(
                    f"--into {target} would create a NESTED worktree inside {enclosing} at "
                    "a path that worktree does NOT ignore, so its next `git add -A` "
                    "records the recovered work as a mode-160000 GITLINK to a commit of "
                    "this repo (invalid in every clone), and the recovery then sits where "
                    f"that worktree's cleanup can delete it. Restore into a path OUTSIDE "
                    f"{enclosing}, or into the main checkout's own ignored lane directory"
                )

        if drop_empty and target.exists():
            # `rmdir` only ever removes an EMPTY directory, and the guards above have all
            # passed, so nothing the lane still holds can be lost by this.
            try:
                target.rmdir()
            except OSError as exc:
                raise LaneWipError(
                    f"{target} is an empty directory that must be removed before the "
                    f"worktree can be recreated there, but it cannot be "
                    f"({exc.strerror or exc}). Remove it yourself (`rmdir {target}`) and "
                    "re-run"
                ) from exc

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
            # ⛔ `_branch_ref`, OR A TAG OF THE SAME NAME SUPPRESSES THE RECREATE. A bare
            # name resolves through git's ref priority, so `refs/tags/<name>` satisfied this
            # probe: the `-b` recreate never ran, `worktree add <path> <tag>` created a
            # DETACHED worktree, the post-add assertion (correctly) failed with exit 3 — and
            # the target path was now occupied, so the retry was refused as "already exists
            # on HEAD". A dead end on the tool's primary verb, for a shape it has dedicated
            # code for. Every other branch read here is qualified; this was the last bare one.
            and not _branch_exists(args.branch, here)
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
                _refuse_unless_holder_usable(holder, args)
                # The holder is assertable (only a passed `--force` gets here): `-f`
                # overrides exactly that stale record and nothing else.
                # ⛔ PASS THE BRANCH AS THE START POINT, NOT THE PARENT SHA. `extra` without
                # `-b` plus `onto=parent` made git DETACH, so the recovered work never
                # reached the branch — measurably so: `rev-parse --abbrev-ref HEAD` was
                # `HEAD`, and after the lane's next commit `git branch --contains HEAD` said
                # `(no branch)` — while the tool printed "created worktree … on <branch>".
                # `onto=None` means `base = args.branch`, i.e. check it out.
                extra = ["-f"]
                onto2: str | None = None
                if args.branch and not _branch_exists(args.branch, here):
                    extra += ["-b", args.branch]
                    onto2 = parent or sha
                add = _add(*extra, onto=onto2)
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
                    _refuse_unless_holder_usable(_branch_holder(args.branch, here), args)
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
            actually = _current_branch(target) or "HEAD"
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
    target_head = _verify_opt("HEAD", cwd=target)
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
    # ⛔ THE GITLINK WARNING BELONGS HERE TOO. `_warn_about_gitlinks` was wired into `snapshot`
    # only, so `recover` printed an unqualified `recovered N file(s)` over a pin whose nested
    # repo came back EMPTY — its commits AND its uncommitted work absent, the third "file"
    # being a bare directory. The docstring of that helper names both verbs as its contract;
    # this is the moment a lane reads the result and decides whether to keep looking.
    _warn_about_gitlinks(sha, root=here)
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

3. If the pin list is EMPTY **in this repository**, the work is genuinely gone HERE — but
   a pin taken with `pin --path <other repo>` lives in THAT repo's ref namespace, so re-run
   `list --all` from there before concluding anything. The only remaining hope is
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


def _make_output_byte_safe() -> None:
    """Never let a MESSAGE kill the verb, and never let it outrun the work.

    ⛔ The INPUT side was fixed long ago (`surrogateescape` on every git read, and a
    `surrogateescape`-capable lane key), but the OUTPUT side was not: `print()` of a path holding a lone
    surrogate raises `UnicodeEncodeError` under a `strict` stdout. That is the default under
    an ordinary `en_US.utf8` locale — this box's `C.UTF-8` hides it — so measured:
    `pin` printed nothing and exited 1 AFTER writing the ref, and `recover` exited 1 AFTER
    `worktree add` but BEFORE `read-tree`, leaving a CLEAN, EMPTY worktree that reads
    exactly like "the pin held nothing". Exit 1 is a code this module reserves as "never a
    verdict about a lane's work"; `sys.stderr` already defaults to `backslashreplace`,
    which is why every refusal survived.
    """
    for stream in (sys.stdout, sys.stderr):
        # not a TextIOWrapper, or already closed
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    _make_output_byte_safe()
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
