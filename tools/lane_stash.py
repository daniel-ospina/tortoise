#!/usr/bin/env python3
"""lane_stash — a LANE-SCOPED stash that cannot take a peer lane's work (#8051).

WHY THIS EXISTS (the root cause, measured — not inferred)
---------------------------------------------------------
``refs/stash`` and its reflog live in the COMMON git dir
(``git rev-parse --git-common-dir``), which every linked worktree of a repo
shares; ``HEAD``, the index and the worktree are per-worktree. So ``stash@{0}``
is a repo-GLOBAL register that every lane writes and that **no lane can see is
shared from the command it types**. A bare ``git stash pop`` in lane A applies
and drops whichever entry ANOTHER lane pushed last:

    lane A: git stash push -m A          -> refs/stash = A
    lane B: git stash push -m B          -> refs/stash = B, A renumbered to @{1}
    lane A: git stash pop                -> applies B, DROPS B        <-- the harm

Three recorded occurrences (#2654, #3539, and a 7860/8482 lane that already
KNEW the hazard). The failure presents as a *successful* pop: no conflict, no
non-zero exit, no warning. The near-miss is worse than a lost stash — the
natural next step is ``git add`` + commit, which lands a peer's uncommitted
work under this lane's branch, message and attribution.

Measured on git 2.50.1 (Apple Git-155), in temp repos, for the design below:

  * ``git stash push`` has NO per-worktree, namespaced or ``--ref`` form.
  * ``git stash create -u`` accepts ``-u`` SILENTLY and emits a 2-parent commit
    — untracked files are NOT captured (and it takes no pathspec, and does not
    clean the tree). It is therefore rejected as the creation primitive: it
    loses fidelity and would force a reimplementation of ``stash push``.
  * ``git stash apply <sha>`` works with the sha NOT on top, restores tracked
    AND untracked, and leaves the shared register and the peer's entry alone.
  * ``git stash drop <sha>`` is refused ("is not a stash reference") — there is
    no hash-addressed drop.
  * ``git log --walk-reflogs --format='%H %gs' refs/stash`` still yields OUR
    commit by a unique message nonce even after a peer pushes on top (``git
    stash push`` does not print the sha, so the nonce is how we identify ours).

THE CONTRACT
------------
* **Creation** uses git's own ``git stash push`` (full fidelity) with a uuid
  nonce in the message, then identifies OUR commit by that nonce in the reflog
  — so a peer's push landing *during* ours cannot mis-identify it — and pins it
  at ``refs/lane/<lane-key>/stash/<n>`` (CAS create; retry on collision).
* **Retrieval never resolves ``stash@{N}``.** ``apply`` / ``pop`` / ``show`` /
  ``drop`` read the lane-scoped ref and act on the SHA, so a peer's entry is
  unreachable by construction and a peer's push cannot renumber a lane ref.
* **A lane key is worktree-specific and BRANCH-INDEPENDENT**: sanitized worktree
  directory basename plus ``sha1(worktree root)[:8]``. Two worktrees (even
  forced onto one branch) never share a ref namespace, AND a lane keeps its
  pins when it checks out another branch inside its own worktree — a routine
  baseline move, and the move that produced the incident this tool exists for.
  Keying on the branch was the first draft; it makes a lane's pins look lost
  the moment it checks out another branch.
* **``pop`` never deletes from the shared register.** The deletion would be
  positional (``refs/stash@{i}``, re-resolved at delete time) and the victim of
  a lost race can be a bare-git lane whose entry is pinned nowhere — i.e.
  exactly the silent loss this tool exists to stop. The entry stays, and a
  SUCCESSFUL apply is recorded durably at ``refs/lane/<key>/applied/<sha>``
  before the lane pin is released (the message marker is written by ``push``
  BEFORE ``_pin`` runs, so the marker alone is never evidence that an entry was
  applied). Reclaim explicitly with ``prune``, which removes an entry only when
  a lane ref pins it or this lane's applied-record names it. Because a
  positional ``reflog delete`` re-resolves ``refs/stash@{i}`` at delete time and
  cannot be made atomic with its verification (git's own ``stash drop`` behaves
  the same), ``prune`` also guard-pins every entry it intends to KEEP and
  re-stores any entry a concurrent push's position shift caused it to remove, so
  every entry it intends to keep and that was present when the prune started
  keeps a handle (a reclaimed entry is collected deliberately: a lane ref pinned
  it or this lane's applied-record names it). Run ``prune`` when the register is
  quiet: an entry PUSHED during the prune is outside that guard set, and because
  git exposes no compare-and-delete, a shift that removes one cannot be undone —
  it is detected by comparing the register's whole sha state against the
  expected one and reported. That comparison is not a proof (a during-prune push
  and a concurrent remover taking the intended sha can cancel out in the
  multiset), so the residual case is documented and pinned by a test rather than
  claimed impossible; see the plan's "Residual race" note.
* **``check`` is fail-closed**: exit 0 when a bare ``git stash pop`` in this
  worktree would take THIS lane's own entry; exit 1 when it would take a FOREIGN
  entry — a peer lane's, identified by the ``[<lane-key>]`` marker this tool
  writes (so two worktrees forced onto ONE branch are still told apart) or by the
  peer's own ``refs/lane`` pin, which OUTRANKS this lane's pin because a
  cross-lane ``recover`` can leave one sha claimed by both lanes — or one whose
  origin cannot be read; exit 2 on a state error. Safe to call from a lane
  pre-flight.

THE TARGET IS NOT A PARAMETER
-----------------------------
Like ``tools/test_lane.py``, there is no ``--lane``/``--ref`` override: a peer's
lane key is a computable ``sha1(path)[:8]``, so any override would be a way for
one lane to delete or adopt another lane's pinned entries. ``push`` appends one
entry (as bare ``git stash push`` must); ``prune`` and ``recover`` are the only
commands that remove or MOVE an existing entry, and both are explicit by design
(`prune` also requires ``--apply``). ``recover`` additionally
REFUSES an entry that another lane's ref or ``[<lane-key>]`` marker names: pinning
it here would leave it claimed by both lanes, and ``check`` would then call a bare
``git stash pop`` safe on work that is not this lane's (#8773 review P1). It also
refuses any entry that is not provably this lane's when NOTHING was dropped —
whether the register ENUMERATES it or ``refs/stash`` still NAMES it (an emptied
reflog) — because `recover` would only mint ownership; pass ``-m`` to assert the
recovery deliberately. A peer's entry is restored with bare ``git stash store`` or
from its own lane.

USAGE
-----
    python tools/lane_stash.py push -u -m "baseline run" -- tortoise/sdk.py
    python tools/lane_stash.py list
    python tools/lane_stash.py pop 1              # apply by sha, keep the shared entry
    python tools/lane_stash.py check              # 0 = a bare pop here is safe
    python tools/lane_stash.py prune --apply      # reclaim pinned/applied register entries
    python tools/lane_stash.py recover <sha>      # the #2654 repair recipe (this lane's own entry)

Exit codes: 0 ok (including "nothing to stash", matching ``git stash push``);
1 ``check`` found a foreign top entry; 2 environment/state error (including a
dangling lane pin or an unreadable register); 3 a git operation failed (push
failure, apply conflict, show failure).
"""
from __future__ import annotations

import sys

# #5128 shape: refuse a <3.12 interpreter before the imports below — a module-level
# 3.12+-only construct would otherwise fail first, and it would fail with a
# traceback instead of this message. The gate that enforces this shape is
# tests/test_entry_point_python_guard.py, not a style rule.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # #5128/#4053: emit the message explicitly and exit 2. `SystemExit(<str>)`
    # always exits 1, and THIS tool publishes 1 as `check`'s HAZARD verdict — so
    # an old interpreter reported through it is indistinguishable, by exit code,
    # from a peer lane's stash entry. 2 is the tool's own environment-error code.
    print(
        f"tools/lane_stash.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/lane_stash.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import hashlib
import json
import os
import re
import subprocess
import uuid
from collections import Counter
from pathlib import Path

REFS_NS = "refs/lane"
MAX_ID_TRIES = 50
#: Reflog entries scanned when locating our own push by nonce. Ours is the
#: newest, so a deep stack still finds it on the first match; the bound keeps a
#: pathological register from turning `push` into an unbounded walk.
LOCATE_WINDOW = 200

#: ``GIT_*`` variables that RETARGET the CLI at another checkout. Inherited from
#: a hook or wrapper they would silently make this tool write lane refs — or
#: apply a stash — against a peer worktree, with no flag involved. Scrubbed for
#: the same reason `tools/test_lane.py` scrubs them.
_RETARGETING_ENV = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
)


class LaneStashError(Exception):
    """A state/usage error (exit 2) — never raised for a plain git failure."""


class GitFailed(Exception):
    """A git operation failed (exit 3), carrying its own returncode."""

    def __init__(self, rc: int, message: str):
        super().__init__(message)
        self.rc = rc


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _RETARGETING_ENV}
    return env


def _git(
    *args: str, check: bool = True, stdin: str | None = None,
) -> subprocess.CompletedProcess:
    cp = subprocess.run(
        # `errors="replace"`: git accepts NON-UTF-8 commit/stash messages (it only
        # warns), and a strict decode made ONE such subject in the repo-global
        # register raise `UnicodeDecodeError` out of every command that read it —
        # a traceback, exit 1 (the HAZARD code), and in `push` it landed AFTER
        # `git stash push`, leaving live UNPINNED work (#8773 review P1). Every
        # parsed structure (ref names, shas, positions, the 32-hex nonce, the
        # `On <branch>:` / `lane_stash ... [key]` markers) is ASCII; subjects are
        # display-only.
        ["git", *args], capture_output=True, text=True, errors="replace",
        env=_env(), input=stdin,
    )
    if check and cp.returncode != 0:
        raise GitFailed(cp.returncode, (cp.stderr or cp.stdout).strip())
    return cp


def _out(*args: str, check: bool = True) -> str:
    return _git(*args, check=check).stdout.strip()


def repo_root() -> Path:
    cp = _git("rev-parse", "--show-toplevel", check=False)
    if cp.returncode != 0:
        raise LaneStashError("not inside a git work tree — nothing to do")
    return Path(cp.stdout.strip())


def git_dir() -> Path:
    return Path(_out("rev-parse", "--absolute-git-dir"))


def common_dir() -> Path:
    return Path(_out("rev-parse", "--git-common-dir")).resolve()


def current_branch() -> str:
    return _out("branch", "--show-current")


def is_linked_worktree() -> bool:
    """True in a linked worktree; False in the main checkout."""
    return git_dir().resolve() != common_dir()


def lane_key(root: Path | None = None) -> str:
    """A worktree-unique, BRANCH-INDEPENDENT lane key.

    The worktree is the isolation unit this tool exists to protect, so the path
    is the identity and the branch is decoration. Keying on the branch was the
    first draft and it is wrong in a way that matters: a lane routinely checks
    out another branch to take a baseline, and a branch-keyed lane would then
    list NOTHING — its pins would look lost while they sat under the old key.
    (The incident that prompted this tool *was* a baseline stash.)

    The digest is load-bearing for the same reason as before: two worktrees can
    share a directory basename, and two worktrees CAN be forced onto one branch
    — a shared ref namespace would reintroduce the collision this tool removes.
    """
    root = root or repo_root()
    # The slug must be a VALID git ref component, not merely a filesystem-safe
    # name: git refuses `..` inside a component and a trailing `.lock`
    # (case-insensitive), and a component may not begin or end with `.`. A
    # worktree directory such as `a..b` or `lane.lock` is legal on disk, so an
    # unsanitized basename makes `_pin`'s `update-ref` fail AFTER `git stash
    # push` already wrote the entry — leaving the lane's work unpinned in the
    # shared register, the exact un-attributable state this tool exists to
    # prevent (#8051 review P2).
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name)[:60]
    slug = slug.replace("..", "-")
    while slug.lower().endswith(".lock"):
        slug = slug[: -len(".lock")]
    slug = slug.strip("-.") or "worktree"
    digest = hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:8]
    return f"{slug}-{digest}"


def ref_ns(key: str) -> str:
    return f"{REFS_NS}/{key}/stash"


def _parse_entries(key: str) -> list[dict]:
    """This lane's pinned entries, ordered by id.

    The REF IS THE REGISTRY: a ref keeps its object reachable against `gc`, is
    name-ordered (`stash/2` after `stash/1`), and is CAS-allocatable — no JSON
    state file whose lost update would orphan live work.
    """
    cp = _git(
        "for-each-ref",
        "--format=%(refname)%00%(objectname)%00%(creatordate:iso-strict)",
        ref_ns(key),
        check=False,
    )
    if cp.returncode != 0:
        raise LaneStashError(cp.stderr.strip() or "cannot read lane refs")
    entries = []
    for line in cp.stdout.splitlines():
        parts = line.split("\0")
        if len(parts) < 2:
            continue
        ref, sha = parts[0], parts[1]
        seg = ref.rsplit("/", 1)[-1]
        if not seg.isdigit():
            continue
        entries.append({
            "id": int(seg),
            "ref": ref,
            "sha": sha,
            "created": parts[2] if len(parts) > 2 else "",
        })
    entries.sort(key=lambda e: e["id"])
    return entries


def _entry(key: str, entry_id: int | None) -> dict:
    entries = _parse_entries(key)
    if not entries:
        raise LaneStashError(
            f"lane {key} has no pinned entries — nothing to act on "
            "(did you use bare `git stash push`? that writes the SHARED register, "
            "which this tool cannot attribute to a lane)"
        )
    if entry_id is None:
        return entries[-1]
    for e in entries:
        if e["id"] == entry_id:
            return e
    raise LaneStashError(
        f"lane {key} has no entry {entry_id} (have: "
        f"{', '.join(str(e['id']) for e in entries)})"
    )


def _commit_alive(sha: str) -> bool:
    return _git("cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def _pin(key: str, sha: str) -> dict:
    """Allocate `refs/lane/<key>/stash/<n>` for `sha` by CAS create.

    The create is `update-ref <ref> <new> ""` — an EMPTY old value means "the
    ref must not exist" (verified: a second create fails with `reference
    already exists`). Two concurrent pushes in one lane therefore cannot
    overwrite each other's pointer; the loser retries the next id.
    """
    entries = _parse_entries(key)
    nxt = (entries[-1]["id"] + 1) if entries else 1
    for _ in range(MAX_ID_TRIES):
        ref = f"{ref_ns(key)}/{nxt}"
        cp = _git("update-ref", ref, sha, "", check=False)
        if cp.returncode == 0:
            return {"id": nxt, "ref": ref, "sha": sha}
        nxt += 1
    raise LaneStashError(f"could not allocate a lane ref under {ref_ns(key)}")


def _pin_or_report(key: str, sha: str) -> dict:
    """Pin `sha` to this lane; on failure say the entry EXISTS and how to recover.

    `_pin` runs AFTER the stash commit is already in the shared register
    (`git stash push` for `push`, `git stash store` for `recover`). Reporting a
    bare allocation failure would read as "the stash was never created" while
    the lane's work sits unpinned and a bare `git stash pop` can take it — so
    the error must name the sha and the recovery recipe (#8051 review P2).
    """
    try:
        return _pin(key, sha)
    except LaneStashError as exc:
        sys.stderr.write(
            f"warning: the stash entry already EXISTS in the shared register as {sha}, "
            f"but it could not be pinned under {ref_ns(key)} ({exc}). A bare "
            "`git stash pop` can take it. Pin it to this lane with:\n"
            f"  python tools/lane_stash.py recover {sha} -m \"<why it is yours>\"\n"
        )
        raise


def _own_sha_by_nonce(nonce: str) -> str | None:
    """Our own stash commit, found by the unique nonce in its message.

    `git stash push` does not print the sha, and `refs/stash` may already be a
    peer's entry by the time we look. The reflog retains OURS, so the nonce is
    the only race-free identifier (measured: still found after a peer pushes on
    top).
    """
    cp = _git(
        "log", "--walk-reflogs", "--format=%H%x00%gs",
        "-n", str(LOCATE_WINDOW), "refs/stash", check=False,
    )
    if cp.returncode != 0:
        return None
    for line in cp.stdout.splitlines():
        parts = line.split("\0")
        if len(parts) >= 2 and nonce in parts[1]:
            return parts[0]
    return None


def _subject_branch(subject: str) -> str | None:
    """The branch a stash message names, or None when it cannot be read.

    `git stash push` writes `WIP on <branch>: <sha> <subject>` (or `On <branch>:
    <msg>` for `store`). This is the standard heuristic — the issue's own
    suggestion — and it is ADVISORY evidence in `check`, never the mechanism:
    retrieval never consults it.
    """
    m = re.match(r"^(?:WIP on|On) (.+?):", subject)
    return m.group(1) if m else None


def _subject_lane_key(subject: str) -> str | None:
    """The lane key a tool-created stash message carries, or None.

    `push` writes `lane_stash <uuid4> [<lane-key>] <label>` into the message, so
    every entry this tool creates is ATTRIBUTABLE to a lane even when two
    worktrees are forced onto ONE branch — the case the branch heuristic cannot
    tell apart, because both lanes are on the same branch (#8051 review P2).
    Anchoring on the 32-hex nonce keeps a user's bracket-containing label from
    being read as the key.
    """
    m = re.search(r"\blane_stash [0-9a-f]{32} \[([^\]]+)\]", subject)
    return m.group(1) if m else None


def _stash_head() -> str | None:
    """The sha `refs/stash` resolves to, or None when the ref does not exist.

    `git stash list` reads the REFLOG; `refs/stash` can still resolve after the
    reflog is expired or unreadable while `stash list` returns rc 0 with no
    rows. `check` must cross-check the two, because "no rows" is NOT the same
    state as "no entry" (#8051 review P1).
    """
    cp = _git("rev-parse", "--verify", "-q", "refs/stash", check=False)
    return cp.stdout.strip() or None


def _shared_register() -> list[dict]:
    """The shared, repo-global register: position, sha, subject, branch, pinned.

    A non-zero rc is a STATE error, not an empty register: collapsing "cannot
    read" into "nothing there" is the fail-open path that reports SAFE while a
    bare `git stash pop` would still take a peer's entry (#8051 review P1).
    """
    cp = _git("stash", "list", "--format=%gd%x00%H%x00%gs", check=False)
    if cp.returncode != 0:
        raise LaneStashError(
            "could not read the shared stash register (`git stash list` failed): "
            f"{(cp.stderr or cp.stdout).strip() or 'no output'}"
        )
    pinned = _pinned_shas()
    rows = []
    for line in cp.stdout.splitlines():
        parts = line.split("\0")
        if len(parts) < 3:
            continue
        rows.append({
            "pos": parts[0],
            "sha": parts[1],
            "subject": parts[2],
            "branch": _subject_branch(parts[2]),
            "pinned": parts[1] in pinned,
        })
    return rows


def _drop_refs_stash(sha: str) -> bool:
    """CAS-drop ``refs/stash`` when it still points at ``sha``.

    Mirrors ``git stash drop`` of the LAST entry: ``git reflog delete
    --updateref`` empties the reflog but does NOT drop the ref when it empties
    (measured), so without this the "removed" entry is still live for bare git
    and ``check`` fails closed in every worktree (#8051 review P2). The expected
    sha guards the delete, so a peer push between the read and the delete leaves
    the ref alone rather than dropping an entry this process never saw.
    """
    return _git("update-ref", "-d", "refs/stash", sha, check=False).returncode == 0


def _register_contains(sha: str) -> bool:
    return sha in {r["sha"] for r in _shared_register()}


def _store_into_register(sha: str, subject: str, key: str) -> bool:
    """``git stash store`` ``sha`` and VERIFY it is in the shared register.

    ``git stash store`` returns 0 WITHOUT creating a reflog entry when
    ``refs/stash`` already points at ``sha`` (measured on git 2.50.1) — exactly
    the state an emptied reflog leaves behind. Reading rc 0 as success therefore
    reports a restoration that never happened, and the retention message's own
    ``recover <sha>`` repair is a no-op that leaves ``check`` fail-closed
    (#8773 review P1).

    The repair — drop the stale ref, then store once more — is taken ONLY in the
    genuine orphan state, where the enumerated register is EMPTY. A ``refs/stash``
    that DIVERGED from a non-empty reflog (a bare ``git reflog delete`` WITHOUT
    ``--updateref`` leaves exactly that) still carries PEER entries, and
    ``update-ref -d`` deletes the whole reflog with them (#8773 review P1); that
    state is refused, never repaired destructively.

    Storing MOVES ``refs/stash``, so a head that the store would leave without a
    handle must be guard-pinned FIRST: when ``refs/stash`` points at a sha the
    register does not enumerate and no other ref names — an unpinned orphan, in
    the empty register OR in a register still holding other entries — repairing
    ``sha`` would otherwise displace it and leave it gc-eligible. ``prune`` already
    protects it; ``recover`` did not (#8773 review P1). A head that another ref
    already names needs no guard (a guard there would be a redundant ref with no
    lifetime, #8773 review P2), and an unpinnable orphan refuses the store
    (fail-closed). Success is the VERIFIED register state, never the exit code.
    """
    head = _stash_head()
    if (
        head is not None
        and head != sha
        # the store below moves refs/stash OFF `head`; if the register does not
        # enumerate it and no other ref names it, it is left with NO handle. This
        # covers the empty register AND the diverged-with-entries register.
        and head not in {r["sha"] for r in _shared_register()}
        and not (_refs_pointing_at(head) - {"refs/stash"})
        and _guard_pin(key, head) is None
    ):
        return False
    cp = _git("stash", "store", "-m", subject, sha, check=False)
    if cp.returncode != 0:
        return False
    if _register_contains(sha):
        return True
    if _shared_register() or _stash_head() != sha:
        return False
    if not _drop_refs_stash(sha):
        return False
    cp = _git("stash", "store", "-m", subject, sha, check=False)
    if cp.returncode == 0 and _register_contains(sha):
        return True
    # The re-store failed after the drop: put the handle back rather than leave
    # the object with neither a ref nor a reflog entry.
    _git("update-ref", "refs/stash", sha, check=False)
    return False


def _pinned_shas() -> set[str]:
    """Every lane pin's object, repo-global.

    A non-zero rc is a STATE error, NEVER an empty set: this feeds the `pinned`
    mark, the retention decision and :func:`_orphan_is_reclaimable`, and an empty
    answer tells the operator an entry "is pinned by no lane ref" when the
    enumeration simply failed (#8773 review P2).
    """
    cp = _git(
        "for-each-ref", "--format=%(objectname)", f"{REFS_NS}/*/stash/*", check=False,
    )
    if cp.returncode != 0:
        raise LaneStashError(
            "could not read the lane pin refs (`git for-each-ref` failed): "
            f"{(cp.stderr or cp.stdout).strip() or 'no output'}"
        )
    return {ln.strip() for ln in cp.stdout.splitlines() if ln.strip()}


def _applied_ref(key: str, sha: str) -> str:
    return f"{REFS_NS}/{key}/applied/{sha}"


def _applied_shas(key: str) -> set[str]:
    """Shas THIS lane has durably recorded as APPLIED (see :func:`_record_applied`).

    The message marker is written by ``push`` BEFORE ``_pin`` runs, so an
    own-marked entry may never have been applied (a pin failure leaves exactly
    that state). Application evidence has to be produced by ``pop`` itself, and
    this namespace is where it lives — a ref, so it is CAS-writable and
    gc-protected like the pins it sits beside (#8773 review P1).
    """
    cp = _git(
        "for-each-ref", "--format=%(objectname)", f"{REFS_NS}/{key}/applied", check=False,
    )
    if cp.returncode != 0:
        # Same fail-closed rule as `_pinned_shas`: an unreadable applied list must
        # not degrade to "this lane applied nothing", which would strip ownership
        # evidence from its own work and print a false "no lane ref names it"
        # (#8773 review P2).
        raise LaneStashError(
            "could not read this lane's applied records (`git for-each-ref` failed): "
            f"{(cp.stderr or cp.stdout).strip() or 'no output'}"
        )
    return {ln.strip() for ln in cp.stdout.splitlines() if ln.strip()}


def _record_applied(key: str, entry: dict) -> None:
    """Move the lane pin to a durable APPLIED record — one atomic CAS transaction.

    Called only AFTER a successful ``git stash apply``. ``update`` (not
    ``create``) on the applied ref makes re-application of the same sha
    idempotent, while the ``delete`` still CAS-checks the pin's exact sha, so a
    concurrent ``pop``/``prune`` cannot have the pin yanked from under us.
    """
    tx = (
        f"update {_applied_ref(key, entry['sha'])} {entry['sha']}\n"
        f"delete {entry['ref']} {entry['sha']}\n"
    )
    cp = _git("update-ref", "--stdin", stdin=tx, check=False)
    if cp.returncode != 0:
        raise LaneStashError(
            f"applied {entry['sha'][:12]} but could not record it as applied and "
            f"release {entry['ref']}: {(cp.stderr or cp.stdout).strip()}"
        )


def _clear_applied(key: str, sha: str) -> None:
    """Drop an applied record after its shared-register entry is gone (best-effort)."""
    _git("update-ref", "-d", _applied_ref(key, sha), sha, check=False)


def _release_stale_applied(key: str, applied: set[str], *, present: set[str]) -> int:
    """Drop applied records whose shared-register entry is gone by ANY route.

    `pop` writes one applied record per cycle, and `_clear_applied` used to run
    only for entries THIS prune removed — so an entry that left the register via a
    peer's `git stash drop`/`clear` or a reflog expiry left its record behind as a
    permanent, gc-blocking ref in the SHARED git dir that `list` never reported
    (#8773 review P2). A record still enumerated in ``present`` is live evidence
    for a later `prune` and is kept.
    """
    stale = sorted(sha for sha in applied if sha not in present)
    for sha in stale:
        _clear_applied(key, sha)
    if stale:
        print(f"released {len(stale)} applied record"
              f"{'s' if len(stale) != 1 else ''} whose shared-register entry is gone")
    return len(stale)


#: Namespace for the prune's temporary GC guard. Deliberately OUTSIDE
#: ``refs/lane`` so no reader of lane ownership (`_pinned_shas`, `_applied_shas`,
#: `_parse_entries`) can ever see it.
GUARD_NS = "refs/pi-lane-prune-guard"


def _guard_pin(key: str, sha: str) -> str | None:
    """Pin ``sha`` so a positional race cannot lose it.

    Three paths create this ref: the entries a prune intends to KEEP (lives for the
    duration of that prune — the ``finally`` unpins it once the sha is back in the
    register); a RETAINED ORPHAN — an emptied register whose ``refs/stash`` no pin
    names — where it is the object's only durable handle; and a DISPLACED head in
    :func:`_store_into_register` that no other ref names, which keeps the object
    reachable across the store.

    Three paths release it: the creating prune's ``finally`` (its own ref only);
    ``recover`` of that sha (``_guard_release_all``, under any key); and
    :func:`_reap_stale_guards` on a later ``prune --apply``, once the OWNER
    worktree is gone AND the object is named by the register or a durable non-guard
    ref other than ``refs/stash`` (a lane pin; ``refs/stash`` itself is movable by
    any later ``stash store``, and another guard is releasable at its owner's
    will). Until one of those runs, a
    guard for an entry nobody adopts is never reached — ``cmd_list`` prints any you
    still hold.

    ``git reflog delete --updateref refs/stash@{i}`` re-resolves the position at
    DELETE time (git's own ``stash drop`` behaves the same), so a peer push
    landing between our verification and the delete shifts every position and
    can remove an entry that is NOT in the doomed set — a peer's unpinned entry,
    whose only handle was the reflog. Pinning the object first makes it
    un-gc-able, so the reconciliation in :func:`cmd_prune` can always re-store
    it (#8773 review P1). The ref is per-lane (and outside ``refs/lane``), so a
    concurrent prune in another lane cannot take this lane's guard away.

    Returns the ref on success, or ``None`` when the object could not be pinned
    (the caller must then refuse to prune — an unprotectable entry must not be
    put at risk).
    """
    ref = f"{GUARD_NS}/{key}/{sha}"
    cp = _git("update-ref", ref, sha, check=False)
    return ref if cp.returncode == 0 else None


def _guard_unpin(ref: str, sha: str) -> None:
    _git("update-ref", "-d", ref, sha, check=False)


def _guard_refs() -> list[tuple[str, str, str]]:
    """Every prune guard ref, as ``(ref, sha, lane_key)`` — under ANY lane key.

    Enumerating only THIS lane's guards hid a guard left by another (possibly
    already-removed) worktree: ``prune --apply`` creates one automatically for a
    retained orphan, and after ``git worktree remove`` that ref survived with no
    command able to name it — a permanent, gc-blocking ref in the shared git dir
    (#8773 review P2).
    """
    cp = _git("for-each-ref", "--format=%(refname) %(objectname)", GUARD_NS,
              check=False)
    if cp.returncode != 0:
        # A failed read is a STATE error, never an empty namespace: collapsing it
        # hid every guard from `list` AND let a prune report "no guards" while the
        # reap silently did nothing (#8822 review P2).
        raise LaneStashError(
            f"could not read the prune-guard refs under {GUARD_NS} "
            f"({cp.stderr.strip() or 'git for-each-ref failed'})"
        )
    out: list[tuple[str, str, str]] = []
    for ln in cp.stdout.splitlines():
        parts = ln.split()
        if len(parts) < 2:
            continue
        ref, sha = parts[0], parts[1]
        owner = (
            ref[len(GUARD_NS) + 1:].split("/", 1)[0]
            if ref.startswith(GUARD_NS + "/") else ""
        )
        out.append((ref, sha, owner))
    return out


def _guard_release_all(sha: str) -> None:
    """Drop every prune guard for ``sha``, under ANY lane key.

    :func:`cmd_recover` is the ONLY caller, and it has just given the object a
    durable lane pin (:func:`_pin_or_report`) — THAT pin is what makes every guard
    for the sha redundant, wherever it lives. Unpinning only the current lane's
    guard left a foreign guard unreachable through ``recover`` for good (#8773
    review P2).

    ⛔ ``prune``'s ``finally`` must NEVER call this. A foreign guard for the same
    sha may belong to a CONCURRENT prune that is still deleting positions, and
    "the sha is back in the register" is exactly the transient state a guard exists
    for (see :func:`_guard_pin` and ``cmd_prune``'s finaliser). "Some ref names the
    object" is NOT on its own a licence to drop every guard (#8822 review P2).
    """
    cp = _git("for-each-ref", "--format=%(refname)", f"{GUARD_NS}/*/{sha}", check=False)
    if cp.returncode != 0:
        return
    for ref in cp.stdout.split():
        _guard_unpin(ref, sha)


def _live_lane_keys() -> tuple[set[str] | None, list[str], str]:
    """``(keys, stale, why)`` for every worktree git knows about.

    ``keys is None`` means the live set could not be DETERMINED, and ``why`` says
    which way: `git worktree list` FAILED (``why`` = "could not be read"), or it
    returned something the parser does not understand (``why`` = "returned a field
    it could not understand …"). Callers must retain everything when ``keys`` is
    ``None`` AND when ``stale`` is non-empty — a worktree moved with plain `mv` (or
    a directory removed without `git worktree prune`) leaves its OLD path
    registered, so the key a LIVE worktree derives from ``--show-toplevel`` would
    be absent and its guards would be classified dead: the wrong direction.

    A prune guard is created under the CREATING worktree's key and outlives that
    worktree; telling a live owner from a removed one is what makes it safe to
    reap a stale guard without touching a concurrent peer prune's protection.
    """
    cp = _git("worktree", "list", "--porcelain", "-z", check=False)
    if cp.returncode != 0:
        return None, [], "could not be read"
    keys: set[str] = set()
    stale: list[str] = []
    current: str | None = None
    # `-z` (NUL-terminated fields) is what makes a path containing a NEWLINE
    # unambiguous: in the newline-separated form such a path splits the record and
    # the live key derived from it is silently absent, so a live owner reads as
    # dead (#8822 review P2).
    for field in cp.stdout.split("\0"):
        if not field:
            continue
        if field.startswith("worktree "):
            current = field[len("worktree "):]
            keys.add(lane_key(Path(current)))
        elif field.startswith("prunable"):
            if current is None:
                return None, [], "returned a `prunable` field before any worktree"
            stale.append(current)
        elif field == "detached" or field.startswith(
            ("HEAD ", "branch ", "locked", "bare")
        ):
            continue
        else:
            # An unrecognised field may be a fragment of an exotic path, or a field
            # a newer git added. Fail CLOSED, and name the parser — not git — as the
            # thing that could not be understood.
            return None, [], (
                f"returned a field it could not understand ({field!r})"
            )
    return keys, stale, ""


def _owner_may_be_live(owner: str, ref: str, sha: str) -> bool:
    """Re-read liveness for ONE candidate; report and answer ``True`` if undecidable.

    The reap's key set is a snapshot, and fleet worktree paths are REUSED: a
    worktree created at a removed path derives the SAME lane key, so its in-flight
    prune's guards would be judged dead. Re-reading narrows that window to a single
    release; ``True`` means "keep", never "release".
    """
    keys, stale, why = _live_lane_keys()
    if keys is None:
        print(f"kept   {ref} for {sha[:12]} — `git worktree list` {why} on re-read, so "
              "a live owner cannot be ruled out")
        return True
    if stale:
        print(f"kept   {ref} for {sha[:12]} — a worktree registration became STALE "
              f"({', '.join(stale)}); run `git worktree prune`, then re-run")
        return True
    return owner in keys


def _release_stale_guard(ref: str, sha: str) -> None:
    _guard_unpin(ref, sha)
    print(f"released stale guard {ref} for {sha[:12]} — its worktree is gone "
          "and the object is named elsewhere")


def _reap_stale_guards() -> None:
    """Release guards whose OWNER WORKTREE is gone and whose object has another handle.

    Before this reap, no routine command released such a guard: `prune`'s
    ``finally`` releases only the guards IT created, and `recover` frees them only
    for the sha it ADOPTS. So a guard for an entry nobody adopts was never reached,
    and left alone it blocks gc forever — which is exactly what this reap exists to
    fix (#8822 review P2).

    Both conditions are required: a LIVE owner key may be a prune in flight, whose
    guard is its only protection during a positional delete; a DEAD owner's guard
    for an object nothing else handles is the LAST handle on that object. "Nothing
    else handles it" is the predicate the body applies — the shared register does
    not enumerate it AND :func:`_refs_pointing_at` returns no other durable
    non-guard ref — never "no other ref names it" alone: a guard whose object the
    register still enumerates is redundant, and this reap releases it.

    Residual windows (documented, not closable with git's ref API): every input is
    a snapshot. The evidence read (the register when it names the object, else
    `_refs_pointing_at` — read only in that branch) is followed by exactly one
    liveness re-read before the `update-ref -d`, so a peer `stash clear` is at most
    one subprocess ahead of the unpin. Liveness is re-read for each CANDIDATE (dead
    owner and another handle), because fleet worktree paths are REUSED: a worktree
    created at a removed path derives the SAME lane key, and without the re-read its
    in-flight prune's guards would be judged dead here.
    """
    guards = _guard_refs()
    if not guards:
        return
    released = 0
    keys, stale, why = _live_lane_keys()
    if keys is None:
        print(f"kept   {len(guards)} prune guard(s) — `git worktree list` {why}, so a "
              "live owner cannot be ruled out")
        return
    if stale:
        # Reported, never silent: a stale registration disables the reap, and the
        # state that CREATES a dead-key guard (a worktree directory removed without
        # `git worktree prune`) is exactly the state that suspends its reaper.
        print(f"kept   {len(guards)} prune guard(s) — a worktree registration is STALE "
              f"({', '.join(stale)}); a stale path can hide a LIVE lane key, so run "
              "`git worktree prune` to enable reaping")
        return
    for ref, sha, owner in guards:
        if not owner or owner in keys:
            continue
        try:
            # Read the register for EACH candidate, immediately before deciding. A
            # peer `stash drop`/`clear` or a reflog expiry between the read and the
            # unpin removes the entry, and deciding from an older read would make
            # the guard we drop the object's LAST handle (#8822 review P1).
            present = {r["sha"] for r in _shared_register()}
        except LaneStashError:
            # `released`, not `i`: `i` is how many candidates were EXAMINED, and the
            # guards skipped as live-owner are still held refs blocking gc. Report
            # the number actually kept (#8822 review P2).
            print(f"kept   {len(guards) - released} prune guard(s) — the shared register "
                  "could not be read, so no further guard was checked")
            return
        if sha in present:
            # `present` is the evidence; `_owner_may_be_live` re-reads liveness
            # (one subprocess) before the unpin, so the register is at most one
            # read old.
            if _owner_may_be_live(owner, ref, sha):
                continue
            _release_stale_guard(ref, sha)
            released += 1
            continue
        others = {
            r for r in _refs_pointing_at(sha)
            # Neither a guard (releasable at its owner's will) nor `refs/stash`
            # (moved by any later `stash store`) is durable backing.
            if r != ref and r != "refs/stash" and not r.startswith(GUARD_NS + "/")
        }
        if not others:
            continue
        # `_refs_pointing_at` is the evidence here, likewise followed by exactly one
        # liveness re-read before the unpin.
        if _owner_may_be_live(owner, ref, sha):
            continue
        _release_stale_guard(ref, sha)
        released += 1


def _refs_pointing_at(sha: str) -> set[str]:
    """Every ref that names ``sha``.

    A non-zero rc is a STATE error, NEVER an empty set: this feeds the ownership
    oracle, and collapsing "cannot read" into "no peer ref names it" is the same
    fail-open the module refuses for `_shared_register` — `check` would report
    SAFE while a bare `git stash pop` took a peer's entry whose ref it could not
    read (#8773 review P2).
    """
    cp = _git("for-each-ref", "--format=%(refname)", f"--points-at={sha}", check=False)
    if cp.returncode != 0:
        raise LaneStashError(
            f"could not enumerate the refs pointing at {sha[:12]} "
            f"(`git for-each-ref --points-at` failed): "
            f"{(cp.stderr or cp.stdout).strip() or 'no output'}"
        )
    return set(cp.stdout.split())


def _lane_key_of_ref(ref: str) -> str | None:
    """The lane key a lane-owned ref names, or ``None`` for any other ref.

    Lane refs are ``refs/lane/<key>/stash/<n>`` and
    ``refs/lane/<key>/applied/<sha>``. ``lane_key`` never contains a ``/`` (its
    slug is sanitised and its digest is hex), so the key is always component 2. A
    guard ref (``refs/pi-lane-prune-guard/<key>/<sha>``) is deliberately NOT
    lane-owned: it keeps an object reachable, it does not claim a lane's
    ownership of it.
    """
    parts = ref.split("/")
    if len(parts) >= 5 and parts[0] == "refs" and parts[1] == "lane":
        return parts[2]
    return None


def _foreign_pin_owners(sha: str, key: str) -> list[str]:
    """Lane keys OTHER than ``key`` whose lane refs name ``sha``.

    A lane ref is written by the lane itself and is branch-independent, so it is
    stronger ownership evidence than the subject's branch — and it survives even
    when THIS lane also pins the same sha, which is exactly the double-pin state a
    cross-lane ``recover`` used to create.
    """
    owners = {
        owner
        for ref in _refs_pointing_at(sha)
        if (owner := _lane_key_of_ref(ref)) is not None and owner != key
    }
    return sorted(owners)


def _reclaim_handle_note(row: dict, key: str) -> str:
    """Where a reclaimed register row's object still lives — for the `remove` line.

    A row reclaimed because ANOTHER lane's ref names it is lossless, but that ref is
    the peer's — invisible to `lane_stash list` here once the register row is gone
    — so the line that removes it must point at where the object still lives.
    Otherwise the only instruction this output gives cannot show the work it just
    reclaimed (#8773 review P2).

    The ref it names must be one that EXISTS, and the instruction must work for THIS
    entry. Three ways the first version was wrong, all reachable (#8822 review P2):
    it printed a literal ``<n>`` placeholder, and `lane_stash pop` with no id
    restores ``entries[-1]`` — so for a peer holding two pins the advice restored
    a DIFFERENT entry (the exact defect class this PR fixes elsewhere); a
    ``refs/lane/<key>/applied/<sha>`` record also makes a lane a
    :func:`_foreign_pin_owners` owner, where no pin exists to pop at all; and it
    appended "`list` here cannot show it" even when THIS lane pins the sha too (the
    legacy double-pin), where `list` here does show it and `pop` here restores it —
    the line withheld the reader's own, more direct handle.
    """
    sha = row["sha"]
    refs = _refs_pointing_at(sha)
    own = sorted(r for r in refs if _lane_key_of_ref(r) == key and "/stash/" in r)
    notes: list[str] = []
    for owner in _foreign_pin_owners(sha, key):
        pins = sorted(r for r in refs if _lane_key_of_ref(r) == owner and "/stash/" in r)
        applied = sorted(
            r for r in refs if _lane_key_of_ref(r) == owner and "/applied/" in r
        )
        if pins:
            notes.append(
                f"{', '.join(pins)} — lane {owner}: `lane_stash pop "
                f"{pins[0].rsplit('/', 1)[1]}` there restores it"
            )
        elif applied:
            notes.append(
                f"{', '.join(applied)} — lane {owner} already restored this work (its "
                "applied record names it)"
            )
        else:
            notes.append(f"a ref of lane {owner}")
    if notes:
        peer = "; ".join(notes)
        if own:
            return ("  (still durable under this lane's " + ", ".join(own)
                    + f" — `lane_stash pop {own[0].rsplit('/', 1)[1]}` here restores "
                    "it; " + peer + ")")
        return ("  (still durable under " + peer
                + "; `list` here cannot show it)")
    if row["pinned"]:
        return "  (still durable under this lane's ref)"
    return "  (applied by this lane; the applied-record named it)"


def _foreign_owner(row: dict, key: str) -> tuple[str, str] | None:
    """``(kind, detail)`` when an entry is provably ANOTHER lane's, else ``None``.

    ``kind`` is ``"ref"`` (another lane's ref names the sha) or ``"marker"`` (the
    message marker names another lane). This is the ONE ownership oracle shared by
    ``list``, ``check`` and ``recover``, so the three cannot disagree about who
    owns an entry.
    """
    foreign = _foreign_pin_owners(row["sha"], key)
    if foreign:
        return "ref", ", ".join(foreign)
    marker = _subject_lane_key(row["subject"])
    if marker is not None and marker != key:
        return "marker", marker
    return None


def _entry_ownership(row: dict, key: str, own_shas: set[str]) -> tuple[str, str]:
    """Classify a shared-register entry against ``key``: own / foreign / unknown.

    ONE classifier for ``list`` and ``check``, so the two can never disagree about
    the same state. The order is fail-closed:

    1. **A foreign lane ref or a foreign message marker is decisive, and it
       OUTRANKS this lane's own refs.** The #8051 incident re-enters through the
       repair door: a lane that ``recover``s a peer's entry ends up pinning the
       same sha, and reading the local pin as ownership made ``check`` print SAFE
       while a bare ``git stash pop`` took the peer's work (#8773 review P1).
    2. This lane's own refs — a ``stash`` PIN or a durable ``applied`` record
       (``pop`` moves the pin to the applied ref, so a marker-less entry this lane
       recovered and then applied is still ITS OWN) — then this lane's own message
       marker are ownership evidence.
    3. Otherwise the entry is UNATTRIBUTABLE and ``check`` fails closed.
    """
    foreign = _foreign_owner(row, key)
    if foreign is not None:
        kind, detail = foreign
        branch = f" (branch {row['branch']!r})" if row.get("branch") else ""
        if kind == "ref":
            return "foreign", (
                f"the top shared entry ({row['sha'][:12]}) is named by another lane's "
                f"ref{branch}: {detail} — a lane ref is that lane's own durable handle, "
                "so a bare `git stash pop` here cannot be shown to take THIS lane's entry"
            )
        return "foreign", (
            f"the top shared entry ({row['sha'][:12]}) was pushed by lane {detail!r} "
            f"(branch {row['branch']!r}), not this lane {key!r} — two worktrees can be "
            "forced onto ONE branch, so a matching branch does not make a bare "
            "`git stash pop` HERE safe: it would apply and drop that lane's entry"
        )
    if _subject_lane_key(row["subject"]) == key or row["sha"] in own_shas:
        return "own", ""
    return "unknown", ""


# ── commands ────────────────────────────────────────────────────────────────

def cmd_push(args: argparse.Namespace) -> int:
    root = repo_root()
    key = lane_key(root)
    nonce = uuid.uuid4().hex
    label = args.message or "wip"
    # The lane marker is written into the message BEFORE `git stash push` runs
    # (and therefore before `_pin` can fail), so it is an OWNERSHIP hint for
    # `check` — never evidence that the entry was applied. Application evidence
    # is the `refs/lane/<key>/applied/<sha>` record `pop` writes (#8773 P1).
    message = f"lane_stash {nonce} [{key}] {label}"

    cmd = ["stash", "push", "-m", message]
    if args.all:
        cmd.append("--all")
    elif args.include_untracked:
        cmd.append("--include-untracked")
    if args.paths:
        cmd.append("--")
        cmd.extend(args.paths)

    cp = _git(*cmd, check=False)
    if cp.returncode != 0:
        sys.stderr.write(cp.stderr or cp.stdout)
        raise GitFailed(cp.returncode, "git stash push failed")

    sha = _own_sha_by_nonce(nonce)
    if sha is None:
        # `git stash push` returns 0 with this message when there was nothing to
        # save. Matching on it (rather than guessing from refs/stash, which a
        # peer may have moved) is what stops this tool from reporting an entry
        # it did not create.
        if "No local changes to save" in cp.stdout:
            print("nothing to stash — worktree clean for the given paths")
            return 0
        raise LaneStashError(
            "git stash push reported success but the entry could not be located "
            "in the shared reflog; run `git stash list` before trusting it"
        )

    entry = _pin_or_report(key, sha)
    print(f"pinned lane entry {entry['id']} [{entry['ref']}]")
    print(f"  sha     {sha}")
    print(f"  subject {_out('log', '-1', '--format=%s', sha)}")
    print(f"  restore python tools/lane_stash.py pop {entry['id']}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    key = lane_key()
    entries = _parse_entries(key)
    scope = "linked worktree" if is_linked_worktree() else "MAIN checkout"
    print(f"lane {key} ({scope})")
    if not entries:
        print("  (no pinned entries)")
    for e in entries:
        alive = "ok" if _commit_alive(e["sha"]) else "MISSING OBJECT"
        subject = _out("log", "-1", "--format=%s", e["sha"]) if _commit_alive(e["sha"]) else "-"
        print(f"  {e['id']:>3}  {e['sha'][:12]}  {alive:<13} {e['created']}  {subject}")

    rows = _shared_register()
    print(f"shared register (refs/stash — repo-global, {len(rows)} entr"
          f"{'y' if len(rows) == 1 else 'ies'})")
    if not rows:
        print("  (empty)")
    # Attribute ownership the SAME way `check` does — by lane key, not by branch.
    # A branch name identifies a branch, never a lane (two worktrees can be forced
    # onto one branch), so a branch-only mark contradicts `check` on the same
    # state: it calls this lane's own pinned entry foreign after a checkout, and
    # a same-branch peer's entry safe (#8773 review P2).
    entries_shas = {e["sha"] for e in entries}
    # `pop` MOVES the pin to the durable applied ref, so this lane's own applied
    # records must count as ownership too — otherwise a marker-less entry this
    # lane recovered and then applied reads FOREIGN? here and HAZARD in `check`,
    # with a reason claiming no ref names it while `refs/lane/<key>/applied/<sha>`
    # does (#8773 review P2).
    own_shas = entries_shas | _applied_shas(key)

    def _mark(r: dict) -> str:
        kind, _reason = _entry_ownership(r, key, own_shas)
        base = {"own": "OWN     ", "foreign": "FOREIGN ", "unknown": "FOREIGN?"}[kind]
        # The pin state is a SUFFIX, never a competing mark: `_pinned_shas()` is
        # repo-global, so a standalone `pinned` mark labelled a PEER's pinned
        # entry exactly like this lane's and the lane-key classifier was never
        # reached (#8773 review P2).
        return f"{base} (pinned)" if r["pinned"] else base

    top = rows[0] if rows else None
    top_own = top is not None and _entry_ownership(top, key, own_shas)[0] == "own"
    for r in rows:
        mark = _mark(r)
        star = ("  <-- a bare `git stash pop` here would take THIS"
                if r is top and not top_own else "")
        print(f"  {r['pos']:<11} {r['sha'][:12]}  {mark}  {r['subject']}{star}")

    register_shas = {r["sha"] for r in rows}
    guards = _guard_refs()
    if guards:
        print(f"prune guards held ({len(guards)} — each keeps its object gc-safe while "
              "nothing else handles it; free one by recovering it, or drop the ref once "
              "you know the work is safe). A guard outlives the worktree that made it, "
              "so FOREIGN lanes are listed here too")
        for ref, sha, owner in guards:
            others = _refs_pointing_at(sha) - {ref}
            peer_guards = sorted(r for r in others if r.startswith(GUARD_NS + "/"))
            durable = sorted(others - {"refs/stash"} - set(peer_guards))
            if durable:
                note = f"  (also named durably by: {', '.join(durable)})"
            elif peer_guards:
                # A guard is a handle its OWNER may release at any time, so two
                # guards for one object are not mutual evidence — presenting them
                # as "durably named" read as a permanent backing that neither has
                # (#8822 review P2).
                note = ("  (also held by another prune guard only — NOT durable; "
                        "either owner may release it)")
            elif sha in register_shas:
                # The SAME retention decision `prune --apply` makes: a guard whose
                # object the shared register still enumerates is redundant, so
                # calling it the handle here contradicted the reap that releases
                # it (#8822 review P2).
                #
                # This arm must sit ABOVE the `refs/stash` one: `refs/stash` names
                # the register's TIP by definition, so with the two swapped this
                # branch could never fire for the commonest leftover guard — and
                # the tip guard kept the "this guard is the handle" claim that the
                # reap then acted against (#8822 re-review P2).
                note = ("  (the shared register still enumerates it, so this guard is "
                        "redundant once its owner is gone — `prune --apply` reaps it)")
            elif "refs/stash" in others:
                note = ("  (refs/stash names it too, but that is NOT durable — a later "
                        "`stash store` moves it; this guard is the handle)")
            else:
                note = ("  (no other ref names it and the register does not enumerate "
                        "it — this guard is the handle)")
            foreign = "" if owner == key else f"  [lane {owner} — not this lane]"
            print(f"  {sha[:12]}  {ref}{foreign}{note}")

    applied = _applied_shas(key)
    if applied:
        print(f"applied records held ({len(applied)} — durable evidence THIS lane restored "
              "the entry; `prune --apply` releases one whose register entry is gone)")
        for sha in sorted(applied):
            print(f"  {sha[:12]}  {_applied_ref(key, sha)}")
    if top is not None and not top_own:
        print("\n⚠️  the top shared entry is not provably this lane's — use `lane_stash pop <id>`, "
              "never a bare `git stash pop` (see `lane_stash check`).")
    return 0


def cmd_apply(args: argparse.Namespace, pop: bool) -> int:
    key = lane_key()
    entry = _entry(key, args.entry)
    if not _commit_alive(entry["sha"]):
        raise LaneStashError(
            f"entry {entry['id']} ({entry['ref']}) points at {entry['sha']}, which is not a "
            "readable stash commit (it is gone from the object database, or it is not a "
            "commit object) — the pinned ref was the last handle, and nothing here can "
            "restore it"
        )
    cmd = ["stash", "apply"]
    if args.index:
        cmd.append("--index")
    cmd.append(entry["sha"])
    cp = _git(*cmd, check=False)
    if cp.returncode != 0:
        sys.stderr.write(cp.stdout or "")
        sys.stderr.write(cp.stderr or "")
        # The lane ref is deliberately NOT touched on failure: a conflicted apply
        # leaves the pin as the only handle on the work.
        raise GitFailed(cp.returncode, "git stash apply failed (entry kept)")
    if pop:
        # CAS-move the pin to a durable APPLIED record: only OUR entry, only if
        # it is still at the sha we applied, and `refs/stash` is never touched
        # (see the module docstring). The record — not the message marker — is
        # what lets a later `prune` prove the work was restored (#8773 P1).
        _record_applied(key, entry)
        print(f"applied and unpinned lane entry {entry['id']} ({entry['sha'][:12]})")
    else:
        print(f"applied lane entry {entry['id']} ({entry['sha'][:12]}) — pin kept")
    print("  the shared register was NOT modified: reclaim pinned entries with "
          "`lane_stash prune --apply`, and never run a bare `git stash pop`.")
    return 0


def cmd_pop(args: argparse.Namespace) -> int:
    return cmd_apply(args, pop=True)


def cmd_show(args: argparse.Namespace) -> int:
    key = lane_key()
    entry = _entry(key, args.entry)
    if not _commit_alive(entry["sha"]):
        raise LaneStashError(
            f"entry {entry['id']} ({entry['ref']}) points at {entry['sha']}, which is not a "
            "readable stash commit (it is gone from the object database, or it is not a "
            "commit object) — there is nothing to show"
        )
    cmd = ["stash", "show"]
    if args.patch:
        cmd.append("-p")
    cmd.append(entry["sha"])
    cp = _git(*cmd, check=False)
    sys.stdout.write(cp.stdout)
    sys.stderr.write(cp.stderr)
    if cp.returncode != 0:
        # Keep the published exit-code set: a git failure is 3, not git's raw
        # status (128), and a dangling pin is the same named state error as
        # `apply`/`pop` (#8773 review P2).
        raise GitFailed(cp.returncode, "git stash show failed")
    return 0


def cmd_drop(args: argparse.Namespace) -> int:
    key = lane_key()
    entry = _entry(key, args.entry)
    if not args.yes:
        raise LaneStashError(
            f"refusing to unpin entry {entry['id']} ({entry['sha'][:12]}) without --yes — "
            "the pin may be the only handle on that work once the shared register is "
            "pruned or its entry is dropped by another lane"
        )
    cp = _git("update-ref", "-d", entry["ref"], entry["sha"], check=False)
    if cp.returncode != 0:
        raise LaneStashError(f"could not unpin {entry['ref']}: {cp.stderr.strip()}")
    print(f"unpinned lane entry {entry['id']} ({entry['sha'][:12]}) — shared register untouched")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Fail-closed detector: would a bare `git stash pop` here take foreign work?"""
    key = lane_key()
    branch = current_branch()
    rows = _shared_register()
    entries = _parse_entries(key)
    head = _stash_head()
    # `pop` moves a lane pin to the durable applied ref, so THIS lane's applied
    # records are ownership evidence too, and they are what makes an unpinned
    # register entry reclaimable by `prune --apply` (#8773 review P2).
    applied = _applied_shas(key)
    unpinned = [r for r in rows if not r["pinned"]]
    reclaimable_unpinned = sum(1 for r in unpinned if r["sha"] in applied)

    verdict = {
        "lane": key,
        "branch": branch,
        "linked_worktree": is_linked_worktree(),
        "lane_entries": len(entries),
        "shared_entries": len(rows),
        "unpinned_shared": len(unpinned),
        "unpinned_reclaimable": reclaimable_unpinned,
        "unpinned_refused": len(unpinned) - reclaimable_unpinned,
        "bare_pop_target": rows[0]["sha"] if rows else None,
        "bare_pop_branch": rows[0]["branch"] if rows else None,
        "safe": True,
        "reasons": [],
    }

    # `git stash list` reads the reflog; `refs/stash` can still resolve when the
    # reflog is expired/unreadable with no rows printed. An empty `rows` is then
    # NOT "nothing to take": a bare `git stash pop` would apply and drop that
    # entry. Fail closed rather than answer from the absence of data (#8051 P1).
    diverged = head is not None and (not rows or rows[0]["sha"] != head)
    # `prune`'s refusal predicate is NARROWER than "the ref is not the register's
    # top entry": it refuses only when the head appears in NO register position.
    # An advisory may name `prune --apply` only where prune would actually run
    # (#8822 review P2).
    prune_refuses = (
        bool(rows) and head is not None and head not in {r["sha"] for r in rows}
    )
    if diverged:
        verdict["safe"] = False
        if not rows:
            verdict["reasons"].append(
                f"refs/stash resolves to {head[:12]} but the shared reflog no longer "
                "enumerates it (an emptied reflog; the read succeeded and returned "
                "nothing) — a bare `git stash pop` here would apply and drop that entry"
            )
        else:
            verdict["reasons"].append(
                f"refs/stash resolves to {head[:12]}, which is not the register's top "
                f"entry ({rows[0]['sha'][:12]}) — the ref and the reflog disagree, so a "
                "bare `git stash pop` here cannot be shown to be safe"
            )

    if rows:
        top = rows[0]
        own_shas = {e["sha"] for e in entries} | applied
        kind, reason = _entry_ownership(top, key, own_shas)
        if kind == "foreign":
            # A foreign lane ref or marker OUTRANKS this lane's own pin: a
            # cross-lane `recover` used to leave the same sha pinned by BOTH
            # lanes, and the local pin was read as ownership — SAFE while a bare
            # `git stash pop` took the peer's work (#8773 review P1).
            verdict["safe"] = False
            verdict["reasons"].append(reason)
        elif kind == "unknown":
            # No lane ref and no marker: the subject's branch is the only evidence
            # left, and a branch name identifies a BRANCH, never a lane.
            if not top["branch"]:
                verdict["safe"] = False
                verdict["reasons"].append(
                    f"the top shared entry ({top['sha'][:12]}) has an unreadable subject "
                    f"({top['subject']!r}) — its origin cannot be established, so a bare "
                    "`git stash pop` cannot be shown to be safe"
                )
            elif top["branch"] != branch:
                verdict["safe"] = False
                verdict["reasons"].append(
                    f"the top shared entry ({top['sha'][:12]}) was pushed on branch "
                    f"{top['branch']!r}, not the current branch {branch!r} — a bare "
                    "`git stash pop` HERE would apply and drop another lane's work"
                )
            else:
                # Marker-less entry whose subject names THIS branch. A branch
                # name identifies a branch, never a lane: two worktrees can be
                # forced onto one branch, and the register predates this tool,
                # so its bare-git entries are marker-less by construction. The
                # old branch-only fallthrough returned SAFE here — the common
                # case, not an edge (#8051 review P2).
                verdict["safe"] = False
                verdict["reasons"].append(
                    f"the top shared entry ({top['sha'][:12]}) carries no lane marker and "
                    f"is pinned by no lane ref, and its subject names the current branch "
                    f"{branch!r} — a branch name identifies a BRANCH, never a lane (two "
                    "worktrees can be forced onto ONE branch), so a bare `git stash pop` "
                    "here cannot be shown to take this lane's own entry"
                )
        # else: own — this lane's marker names it, or this lane's ref pins it, and
        # no other lane's ref or marker does. That is ownership evidence even when
        # the pin is gone: `pop` deliberately leaves the shared entry in place
        # after releasing the lane ref, so a bare pop here would take this lane's
        # own (already-restored) work.

    if args.json:
        print(json.dumps(verdict, indent=2))
    else:
        where = "linked worktree" if verdict["linked_worktree"] else "MAIN checkout"
        print(f"lane {key} ({where}), branch {branch!r}")
        if rows:
            print(f"top shared entry: {rows[0]['pos']} {rows[0]['sha'][:12]}  "
                  f"branch={rows[0]['branch']!r}  {rows[0]['subject']}")
        else:
            print("shared register: empty")
        if unpinned:
            # Report the SAME retention decision `prune` makes: an unpinned entry
            # THIS lane recorded applied IS reclaimable, so calling all of them
            # "refused" contradicted prune on the state `pop` creates by design
            # (#8773 review P2).
            parts = []
            if reclaimable_unpinned:
                if prune_refuses:
                    # Naming `prune --apply` here would be the same contradiction
                    # this advisory exists to remove: a diverged register makes
                    # prune exit 2 (#8822 review P2).
                    parts.append(
                        f"{reclaimable_unpinned} reclaimable once the diverged register "
                        "is reconciled — `prune --apply` refuses it as it stands")
                else:
                    parts.append(
                        f"{reclaimable_unpinned} reclaimable by `prune --apply` (this lane "
                        f"recorded {'it' if reclaimable_unpinned == 1 else 'them'} applied)")
            refused = len(unpinned) - reclaimable_unpinned
            if refused:
                parts.append(f"{refused} refused (no handle)")
            print(f"  note: {len(unpinned)} shared entr"
                  f"{'y is' if len(unpinned) == 1 else 'ies are'} pinned by no lane ref: "
                  + "; ".join(parts))
        if verdict["safe"]:
            if not rows and head is None:
                # SAFE with an empty register is true for a DIFFERENT reason: there
                # is no own entry, there is nothing at all (#8822 review P2).
                print("VERDICT: SAFE — the shared register is empty; a bare `git stash "
                      "pop` has nothing to take")
            else:
                # "THIS lane's own", never "this branch's": a branch name identifies
                # a BRANCH, never a lane — the framing every HAZARD reason refutes
                # (#8773 review P2).
                print("VERDICT: SAFE — a bare `git stash pop` here would take THIS "
                      "lane's own entry")
        else:
            print("VERDICT: HAZARD — do not run a bare `git stash pop`/`git stash drop` here")
            for r in verdict["reasons"]:
                print(f"  - {r}")
            print("  use: python tools/lane_stash.py list ; python tools/lane_stash.py pop <id>")
    return 0 if verdict["safe"] else 1


def cmd_recover(args: argparse.Namespace) -> int:
    """The #2654 repair recipe: restore a dropped entry AND pin it this time."""
    if _git("cat-file", "-t", args.sha, check=False).returncode != 0:
        raise LaneStashError(f"{args.sha} is not an object in this repository")
    if _git("cat-file", "-t", args.sha, check=False).stdout.strip() != "commit":
        raise LaneStashError(f"{args.sha} is not a commit — `git stash store` needs a stash commit")
    # Normalise to the FULL sha BEFORE any register or guard lookup. The register
    # enumerates full shas, so exact-string membership over an abbreviated sha can
    # never match: the store would succeed yet be read as failure, leaving the
    # entry UNPINNED while telling the user it is absent — and the next bare
    # `git stash pop` takes it (#8773 review P1). `cat-file -t` above already
    # proved the name resolves unambiguously.
    args.sha = _out("rev-parse", args.sha)
    parents = _out("rev-list", "--parents", "-n", "1", args.sha).split()
    subject = _out("log", "-1", "--format=%s", args.sha)
    commit_subject = subject
    # A stash commit has 2 parents (HEAD + index) or 3 with `-u`/`-a` (the
    # untracked tree). A plain commit has 1; a 2-parent merge has 2 and would
    # pass a parent count alone, so the SUBJECT must also look like a stash
    # unless the operator asserts it with -m (an explicit human claim, which is
    # the only thing that lets a store of a rescued entry proceed).
    if len(parents) not in (3, 4):
        raise LaneStashError(
            f"{args.sha} has {len(parents) - 1} parent(s); a stash commit has 2 (HEAD + "
            "index) or 3 (with -u/-a), so this is not a stash entry"
        )
    if not args.message and not _subject_branch(subject):
        raise LaneStashError(
            f"{args.sha}'s subject ({subject!r}) does not name a branch, so it cannot be "
            "shown to be a stash entry — pass -m <message> to assert it explicitly"
        )
    subject = args.message or subject or f"recovered {args.sha}"
    key = lane_key()
    # `recover` is the ONE command that can put an ARBITRARY sha under this lane's
    # key, so it is the repair door the #8051 incident can re-enter through. A
    # lane that recovers a peer's entry ends up pinning a sha the peer's own ref
    # already names; `check` then read the local pin as ownership, printed SAFE,
    # and a bare `git stash pop` took the peer's work (#8773 review P1). Refuse
    # anything provably another lane's — the object's own subject (never the `-m`
    # override) plus the refs pointing at it.
    foreign = _foreign_owner(
        {"sha": args.sha, "subject": commit_subject,
         "branch": _subject_branch(commit_subject)},
        key,
    )
    if foreign is not None:
        kind, detail = foreign
        which = (f"it is named by another lane's ref: {detail}" if kind == "ref"
                 else f"its message says it was pushed by lane {detail!r}")
        raise LaneStashError(
            f"refusing to recover {args.sha[:12]}: {which}. `recover` pins the entry under "
            f"THIS lane's key ({key!r}), so adopting a peer lane's entry here would leave "
            "it claimed by both lanes — and `check`, reading this lane's pin as ownership, "
            "would then report SAFE on a bare `git stash pop` that takes that lane's work. "
            "Recover the entry from the lane that owns it — its `[<lane-key>]` marker "
            "matches that lane's key there — or restore it deliberately with bare "
            "`git stash store`."
        )
    # An entry that is ALREADY enumerated by the register was never dropped, so
    # `recover` has nothing to restore — it would only MINT this lane's ownership
    # of an entry that may be another lane's, and `check` would then clear a bare
    # `git stash pop` on it. A bare-git push is marker-less and unpinned, i.e.
    # exactly the #2654/#3539 class, so this closes the last adoption door. An
    # ENUMERATED OWN entry (a pin-failure repair) and every emptiness/divergence
    # repair are unaffected: own entries are `own`, and those repairs are not
    # enumerated. `-m` is the explicit human assertion that overrides it, as it
    # already is for a subject that does not look like a stash (#8773 P1).
    # Provenance oracle shared by both refusal doors: an own pin/applied record, or
    # an own `[<lane-key>]` marker. Computed once, ABOVE the doors, so the
    # `refs/stash`-still-NAMES door can tell an own entry from a stranger's (#8822
    # review P2).
    own_shas = {e["sha"] for e in _parse_entries(key)} | _applied_shas(key)
    if not args.message:
        for row in _shared_register():
            if row["sha"] != args.sha:
                continue
            if _entry_ownership(row, key, own_shas)[0] != "own":
                raise LaneStashError(
                    f"refusing to recover {args.sha[:12]}: it is ALREADY in the shared "
                    f"register (position {row['pos']}) and is not provably this lane's — "
                    "nothing was dropped, so there is nothing to recover. `recover` would "
                    "PIN it to this lane, and `check` would then clear a bare `git stash "
                    "pop` on another lane's work. Restore it with `git stash apply <sha>` "
                    "(it works for any sha), or pass -m <message> to assert the recovery "
                    "explicitly."
                )
            break
    # An entry `refs/stash` still NAMES is equally un-dropped: the register
    # enumerating NOTHING does not mean the work was lost — an emptied reflog
    # (`git reflog expire --expire=now --all`) leaves the ref on a sha the register
    # no longer lists, the routine-maintenance state `_reclaim_orphan_refs_stash`
    # exists for. In that state a peer's bare `git stash push` carries no
    # `[<lane-key>]` marker and no lane ref names it, so nothing proves whose it
    # is: `recover` would MINT this lane's ownership and `check` would then clear
    # a bare `git stash pop` on a peer's work — the same #8051 harm the ENUMERATED
    # guard above closes, reached through the other door. Recovery there is an
    # ASSERTION of ownership, so it requires `-m` (#8773 review P1) — but only for
    # an entry that is NOT already provably this lane's: demanding `-m` for an own
    # pin or an own marker asks the operator to assert ownership the tool already
    # holds, and the refusal text ("no lane marker and no lane ref") would be
    # false about that entry (#8822 review P2).
    provably_own = args.sha in own_shas or _subject_lane_key(commit_subject) == key
    if (
        not args.message
        and not provably_own
        and _stash_head() == args.sha
        and not _register_contains(args.sha)
    ):
        raise LaneStashError(
            f"refusing to recover {args.sha[:12]}: `refs/stash` still NAMES it, so nothing "
            "was dropped — and it carries no lane marker and no lane ref, so it cannot be "
            "shown to be yours. `recover` would PIN it to this lane, and `check` would then "
            "clear a bare `git stash pop` on it. Pass -m <why this entry is yours> to "
            "assert the recovery explicitly, or restore it with `git stash apply <sha>` "
            "(which works for any sha)."
        )
    if not _store_into_register(args.sha, subject, key):
        raise LaneStashError(
            f"could not put {args.sha} into the shared register. Either `git stash store` "
            "failed, or `refs/stash` DIVERGED from a non-empty reflog (a bare `git reflog "
            "delete` without --updateref leaves that) — repairing that would delete the "
            "whole reflog, including the peer entries `git stash list` still shows, so it "
            "is refused. Do NOT drop refs/stash to 'fix' it: recover the other entries "
            "first, or leave it to `check` (which fails closed on it)."
        )
    entry = _pin_or_report(key, args.sha)
    # The object now has a lane pin, so a prune guard for it is redundant — under
    # ANY lane key, because the entry may have been retained by another lane's
    # prune and a guard left here would keep the object un-gc-able for good
    # (#8773 review P2).
    _guard_release_all(args.sha)
    print(f"restored {args.sha} to the shared register (subject: {subject!r})")
    print(f"pinned lane entry {entry['id']} [{entry['ref']}] for lane {key}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """Reclaim shared-register entries this lane can PROVE were restored (lossless).

    The proof is a lane pin or this lane's durable applied-record — never the
    message marker, which `push` writes before `_pin` can fail (#8773 P1).
    """
    key = lane_key()
    rows = _shared_register()
    head = _stash_head()

    applied = _applied_shas(key)

    def reclaimable(r: dict) -> bool:
        # A lane ref pins the object, so removing the register entry is lossless.
        # An UNPINNED entry is removable only when THIS lane left a durable
        # applied-record for its sha: `pop` writes that record on a successful
        # apply. The message MARKER is not evidence — `push` writes it BEFORE
        # `_pin` runs, so a pin-failure entry is own-marked, unpinned and never
        # applied, and deleting its register entry destroys the only handle to
        # that work (#8773 review P1).
        return bool(r["pinned"]) or r["sha"] in applied

    doomed = [r for r in rows if reclaimable(r)]
    kept = [r for r in rows if not reclaimable(r)]
    # A register can DIVERGE from its ref: a bare `git reflog delete` without
    # `--updateref` leaves `refs/stash` on a sha the register no longer
    # enumerates. `reflog delete --updateref` (used below) MOVES the ref to the
    # reflog's new top, and that head is named by no other ref — a
    # single-threaded, silent loss of a peer's bare stash, invisible to the
    # register-derived sets. It cannot be re-listed either (`stash store` is a
    # no-op while the ref already equals the sha, and dropping the ref would
    # delete the whole reflog with it), so a diverged register is REFUSED below
    # (#8773 reviews P1, P2).
    register_shas = {r["sha"] for r in rows}
    diverged_head = head if head is not None and head not in register_shas else None
    # The state an emptied reflog leaves behind: `refs/stash` resolves while the
    # register enumerates nothing (routine maintenance creates it).
    orphan = head is not None and not rows
    print(f"shared register: {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}; "
          f"{len(doomed)} removable (a lane ref pins it, or this lane recorded it "
          f"applied), {len(kept)} unpinned (refused)")
    for r in doomed:
        print(f"  remove {r['pos']} {r['sha'][:12]}  {r['subject']}"
              f"{_reclaim_handle_note(r, key)}")
    for r in kept:
        print(f"  keep   {r['pos']} {r['sha'][:12]}  {r['subject']}  (no lane ref pins it)")
    if orphan:
        print(f"  orphan refs/stash: {head[:12]} resolves while the register enumerates "
              "nothing — an emptied reflog (bare git can still `git stash pop` it)")
    elif diverged_head is not None:
        print(f"  diverged refs/stash: {diverged_head[:12]} is not enumerated by the "
              "register (a bare `git reflog delete` without --updateref leaves that) — a "
              "positional delete would move the ref off it, so this prune REFUSES")
    # A diverged register cannot be pruned safely, whether or not other refs name
    # the head: `reflog delete --updateref` MOVES the ref to the reflog's new top,
    # the head then cannot be re-listed (`stash store` no-ops while the ref
    # already equals the sha, and dropping the ref would delete the whole reflog
    # with it), and the positional pre-check misreads `stash@{0}` while the ref
    # diverges. So refuse outright — before the `not doomed` return, which would
    # otherwise exit 0 on the very state this refuses (#8773 reviews P1, P2).
    if diverged_head is not None and not orphan:
        raise LaneStashError(
            f"refs/stash points at {diverged_head[:12]}, which the shared register does not "
            "enumerate — the ref and its reflog have DIVERGED (a bare `git reflog delete` "
            "without --updateref leaves that). A positional delete would MOVE the ref to "
            "the reflog's new top and that head cannot be re-listed, so this prune refuses "
            "to touch the register at all. `check` stays fail-closed on this state; inspect "
            "it with `lane_stash list`, or reconcile the ref deliberately with git once you "
            "know the entry is safe."
        )
    if args.apply:
        # Before this reap, no routine command released a removed worktree's guard:
        # `prune`'s `finally` releases only guards IT created, and `recover` frees
        # them only for the sha it adopts (#8822 review P2).
        _reap_stale_guards()
    if not doomed:
        if not orphan:
            # Nothing to reclaim — but an APPLIED record whose register entry left
            # by another route (a peer's `stash drop`/`clear`, reflog expiry) is
            # still a live gc-blocking ref in the SHARED git dir, and nothing else
            # reaps it (#8773 review P2).
            if args.apply:
                _release_stale_applied(key, applied, present=register_shas)
            return 0
        rc = _reclaim_orphan_refs_stash(head, key=key, apply=args.apply)
        if args.apply:
            # The orphan's OWN record is the reclaimability proof just consumed
            # above, so sweep only AFTER it; any other stale record goes now.
            try:
                present = {x["sha"] for x in _shared_register()}
            except LaneStashError:
                present = None
            if present is not None:
                _release_stale_applied(key, applied, present=present)
        return rc
    if not args.apply:
        print("dry run — re-run with --apply to remove the pinned entries")
        return 0
    # GC guard for every entry this prune intends to KEEP (see `_guard_pin`).
    # `reflog delete` re-resolves `refs/stash@{i}` at DELETE time, so a peer push
    # landing between our verification and the delete shifts every position and
    # can remove a KEPT entry — a peer's unpinned one, whose only handle was the
    # reflog. The guard makes that object un-gc-able; the reconciliation below
    # re-stores anything the shift removed (#8773 review P1).
    guarded: dict[str, tuple[str, str]] = {}
    for r in kept:
        ref = _guard_pin(key, r["sha"])
        if ref is None:
            # Cannot guarantee this entry survives a position shift — refuse to
            # delete anything rather than prune without the guard in place.
            for s, (gr, _sub) in guarded.items():
                _guard_unpin(gr, s)
            raise LaneStashError(
                f"could not guard {r['sha'][:12]} under {GUARD_NS}/{key} before pruning — "
                "refusing to remove anything while a kept entry cannot be protected"
            )
        guarded[r["sha"]] = (ref, r["subject"])
    try:
        # Positions are walked HIGH -> LOW so an earlier delete cannot renumber a
        # later specifier. `reflog delete` re-resolves `refs/stash@{i}` at DELETE
        # time, so the sha at that position is re-verified immediately before each
        # delete; if a peer pushed in between, this prune stops removing and falls
        # through to the reconciliation instead of trusting a stale position.
        order = sorted(doomed, key=lambda r: int(r["pos"].rsplit("@{", 1)[-1].rstrip("}")))
        # The full sha state we believe the register holds, as a COUNTER: an
        # entry must be removable one occurrence at a time. Duplicates are
        # reachable (`recover <sha>` of a sha already in the register prepends
        # it), and a list comprehension that drops EVERY occurrence would read
        # a correct delete of one of them as a concurrency event (#8773 review
        # P2). Every delete must leave the state exactly one intended sha
        # smaller; anything else means a peer (or another lane's prune)
        # interleaved.
        expected = Counter(r["sha"] for r in rows)
        removed = 0
        for r in reversed(order):
            spec = r["pos"]
            # Re-snapshot: abort before touching anything if a peer has already
            # changed the register since the previous step.
            if Counter(x["sha"] for x in _shared_register()) != expected:
                print("STOP: the shared register changed during the prune (a peer pushed "
                      "or reclaimed an entry); re-run when it is quiet")
                break
            here = _out("rev-parse", spec, check=False)
            if here != r["sha"]:
                print(f"STOP: {spec} is now {here or 'gone'} (expected {r['sha']}) — a peer "
                      "pushed during the prune; re-run when the register is quiet")
                break
            cp = _git("reflog", "delete", "--updateref", spec, check=False)
            if cp.returncode != 0:
                print(f"could not remove {spec}: {cp.stderr.strip()}")
                continue
            # Did the register end up exactly where a clean delete of `r["sha"]`
            # would leave it? `git reflog delete` re-resolves the position at
            # DELETE time, so a push (or another lane's prune) inside this call
            # shifts positions; comparing the WHOLE state detects every shift
            # that changes the sha multiset. It is NOT a proof: a during-prune
            # push and a concurrent remover taking the intended sha can cancel
            # out, leaving `post == want` while the unseen during-prune entry was
            # the one removed. Git exposes no compare-and-delete and the two
            # histories are observationally identical, so that residual cannot be
            # detected here — it is documented in the plan ("Residual race") and
            # pinned by test_prune_cannot_distinguish_a_cancelled_shift. It is
            # precisely why `prune` must run only when the register is quiet.
            # A bare "is the intended sha gone?" check would be strictly weaker:
            # a concurrent remover alone can take that sha while our delete
            # removes a peer's entry, which the whole-state comparison DOES catch.
            want = expected - Counter({r["sha"]: 1})
            post = Counter(x["sha"] for x in _shared_register())
            if post != want:
                print(f"WARNING: the shared register changed while this prune deleted "
                      f"{spec} — a concurrent push or another prune interleaved, so the "
                      "entry removed may not be the intended one. An entry pushed DURING "
                      "a prune is outside the guard set and cannot be restored here; "
                      "re-check `git stash list` (a removed commit normally survives in "
                      "the object DB until gc — `git fsck --unreachable` lists it) and "
                      "re-run the prune when the register is quiet.")
                break
            removed += 1
            expected = want
        print(f"removed {removed} pinned shared-register entr"
              f"{'y' if removed == 1 else 'ies'}; {len(kept)} unpinned left untouched")
        # `reflog delete --updateref` empties the reflog but git does NOT drop the
        # ref when it empties (measured). If this prune took the LAST entry the ref
        # still resolves, so the "removed" entry stays live for bare git and `check`
        # fails closed with no command able to clear it. Mirror `git stash drop`'s
        # last-entry behaviour (#8051 review P2; the plan's row 8 said otherwise).
        if not _shared_register():
            drop_head = _stash_head()
            if drop_head is not None:
                _reclaim_orphan_refs_stash(drop_head, key=key, apply=True)
        # Reconciliation — runs on EVERY exit path (the STOP `break` included, and
        # even after a failed delete). A KEPT sha that is no longer in the register
        # was removed by a position shift caused by a concurrent push (the
        # verify/delete window is not atomic and cannot be made so with git's
        # positional reflog delete). Its object is still held by its guard ref, so
        # re-store it with its original reflog subject — the prune must not lose a
        # peer's only handle (#8773 review P1).
        live = {x["sha"] for x in _shared_register()}
        restored: list[str] = []
        for sha, (_ref, subject) in guarded.items():
            if sha in live:
                continue
            if _store_into_register(sha, subject or f"recovered {sha}", key):
                restored.append(sha)
        if restored:
            print(f"RESTORED {len(restored)} shared-register entr"
                  f"{'y' if len(restored) == 1 else 'ies'} a concurrent push caused this "
                  "prune to remove in error — re-check the register with `lane_stash list`")
        # Applied-records for entries whose register entry is gone by ANY route —
        # this prune's own removal, a peer's `stash drop`/`clear`, or a reflog
        # expiry. A record with no register entry is a permanent, gc-blocking ref
        # in the SHARED git dir and nothing else reports it (#8773 review P2).
        # `live` is read AFTER the reconciliation, so a re-stored kept entry is
        # present and its record is kept. The last entry's record was already
        # consumed by the orphan handling above, which is why this runs there and
        # not before it.
        _release_stale_applied(key, applied, present=live)
    finally:
        # Unpin only guards whose object is back in the register. A guard for a
        # sha that is NOT in the register is the LAST handle on that object, so it
        # is deliberately kept (a failed re-store must not make the object
        # gc-eligible); the printed warning says how to restore it (#8773 P1).
        # `None` means the read FAILED — retain conservatively and say so, never
        # claim the entry is missing (#8773 review P2).
        final: set[str] | None
        try:
            final = {x["sha"] for x in _shared_register()}
        except LaneStashError:
            final = None
        for sha, (guard_ref, _subject) in guarded.items():
            if final is not None and sha in final:
                # Release ONLY this prune's own guard. A FOREIGN guard for the same
                # sha may belong to a CONCURRENT prune in another lane that still
                # has to finish deleting positions — being "in the register" is
                # exactly the transient state a guard exists for, so releasing it
                # under every key would strip a live peer's protection. A guard
                # orphaned by a removed worktree is still enumerable by `list` and
                # released by `recover` once the object has a durable lane pin
                # (#8773 review P2).
                _guard_unpin(guard_ref, sha)
            elif final is None:
                print(f"kept   guard ref {guard_ref} for {sha[:12]} — the shared register "
                      "could NOT be read, so this guard is retained conservatively; check "
                      "`lane_stash list` before recovering")
            else:
                print(f"kept   guard ref {guard_ref} for {sha[:12]} — it is not back in the "
                      "shared register, so this ref is the last handle on it; restore "
                      f"with\n  python tools/lane_stash.py recover {sha} -m \"<why it is yours>\"")
    return 0


def _orphan_is_reclaimable(sha: str, key: str) -> bool:
    """Whether an emptied-register `refs/stash` may be dropped without losing work.

    Same evidence rule as :func:`cmd_prune.reclaimable`: a pin, or THIS lane's
    durable applied-record. The message marker is not evidence — an own-marked
    entry that never applied (a pin failure) would otherwise be dropped here
    with no handle left (#8773 review P1).
    """
    return sha in _pinned_shas() or sha in _applied_shas(key)


def _reclaim_orphan_refs_stash(sha: str, *, key: str, apply: bool) -> int:
    """Drop an emptied-register `refs/stash` (the last-entry cleanup).

    Gated: an emptied reflog can leave `refs/stash` pointing at an entry NO lane
    ref pins. Dropping the ref then destroys the only handle a peer's bare
    `git stash pop` still has (the commit stays in the object DB but becomes
    gc-eligible), so the drop happens only when a lane ref pins `sha` (lossless)
    or THIS lane's durable applied-record names it (the work was already
    restored by `pop`). The message marker is NOT application evidence — `push`
    writes it before `_pin` can fail (#8773 review P1). Otherwise the orphan is
    reported and left alone.
    """
    if not _orphan_is_reclaimable(sha, key):
        if not apply:
            # A dry run must MUTATE nothing — creating the guard ref here made an
            # inspection command write to the ref store, and that guard is then
            # only clearable by `recover` or a manual `update-ref -d` (#8773 P2).
            print(f"dry run — refs/stash {sha[:12]} would be KEPT: it is an unpinned orphan "
                  "(the register enumerates nothing, no lane ref pins it, and this lane "
                  "has no applied-record for it), and dropping the ref would destroy the "
                  "last handle bare git has on that entry. Re-run with --apply to guard-pin "
                  "the object and keep it (it is never dropped); if the work is yours, "
                  "recover it with\n"
                  f"  python tools/lane_stash.py recover {sha} -m \"<why it is yours>\"")
            return 0
        # `refs/stash` alone is NOT a durable handle: the caller's reconciliation
        # runs `git stash store` after this, which moves the ref and would destroy
        # exactly the handle this message promises (#8773 review P2). Pin the
        # object instead; the guard ref is per-lane and nothing unpins it, so the
        # orphan survives the store.
        ref = _guard_pin(key, sha)
        held = (f"held by {ref}" if ref else
                "held ONLY by refs/stash (it could NOT be guard-pinned, so a later "
                "`stash store` may displace it)")
        print(f"kept   refs/stash {sha[:12]}  (unpinned orphan: the register enumerates "
              "nothing, no lane ref pins it, and this lane has no applied-record for "
              "it — dropping the ref would destroy the last handle bare git has on "
              "that entry)\n"
              f"  {held} — this is a HAZARD state; a guard ref keeps the object "
              "reachable even if another `stash store` moves refs/stash; `lane_stash "
              "check` stays fail-closed here. If the work is yours, recover it with:\n"
              f"    python tools/lane_stash.py recover {sha} -m \"<why it is yours>\"")
        return 0
    if not apply:
        print(f"dry run — refs/stash resolves to {sha[:12]} while the register enumerates "
              "nothing; re-run with --apply to drop it (mirrors `git stash drop` of the "
              "last entry)")
        return 0
    if _drop_refs_stash(sha):
        if sha in _applied_shas(key):
            _clear_applied(key, sha)
        print(f"dropped refs/stash ({sha[:12]}) — the register is now empty, as `git stash "
              "drop` of the last entry leaves it")
        return 0
    print(f"warning: the register is empty but refs/stash is no longer {sha[:12]} — a peer "
          "moved it during the prune; left in place")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lane_stash",
        description="A lane-scoped stash: never applies or drops a peer lane's entry (#8051).",
        epilog="Exit codes: 0 ok (incl. 'nothing to stash'); 1 check found a foreign top "
               "entry; 2 state/usage error; 3 a git operation failed.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("push", help="stash in this worktree and pin the entry to this lane")
    sp.add_argument("-m", "--message", help="human label for the entry")
    sp.add_argument("-u", "--include-untracked", action="store_true")
    sp.add_argument("-a", "--all", action="store_true", help="include ignored files")
    sp.add_argument("paths", nargs="*", help="optional pathspec (after `--`)")
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("list", help="this lane's pinned entries + the shared register")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("apply", help="apply a lane entry by sha, keeping the pin")
    sp.add_argument("entry", nargs="?", type=int, help="lane entry id (default: newest)")
    sp.add_argument("--index", action="store_true", help="also restore the index")
    sp.set_defaults(func=lambda a: cmd_apply(a, pop=False))

    sp = sub.add_parser("pop", help="apply a lane entry by sha and unpin it (shared register untouched)")
    sp.add_argument("entry", nargs="?", type=int, help="lane entry id (default: newest)")
    sp.add_argument("--index", action="store_true", help="also restore the index")
    sp.set_defaults(func=cmd_pop)

    sp = sub.add_parser("show", help="git stash show for a lane entry")
    sp.add_argument("entry", nargs="?", type=int, help="lane entry id (default: newest)")
    sp.add_argument("-p", "--patch", action="store_true", help="show the full patch")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("drop", help="unpin a lane entry WITHOUT applying it")
    sp.add_argument("entry", nargs="?", type=int)
    sp.add_argument("--yes", action="store_true", help="required: the pin may be the only handle")
    sp.set_defaults(func=cmd_drop)

    sp = sub.add_parser("check", help="would a bare `git stash pop` here take foreign work?")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("recover", help="restore a dropped stash commit to the register AND pin it")
    sp.add_argument("sha")
    sp.add_argument("-m", "--message", help="subject for the restored entry (default: the commit's own)")
    sp.set_defaults(func=cmd_recover)

    sp = sub.add_parser("prune", help="reclaim shared-register entries pinned by a lane ref")
    sp.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    sp.set_defaults(func=cmd_prune)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.paths = getattr(args, "paths", None) or []
    try:
        return args.func(args)
    except GitFailed as exc:
        sys.stderr.write(f"git failed (rc={exc.rc}): {exc}\n")
        return 3
    except LaneStashError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
