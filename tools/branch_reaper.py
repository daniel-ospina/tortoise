#!/usr/bin/env python3
"""branch_reaper — reap provably-dead LOCAL branches, classified from GitHub PR state (#4408).

Why this exists
---------------
This repo squash-merges, so `git branch --merged origin/main` badly undercounts:
a branch whose PR landed is not an ancestor of ``main``. Classifying by Git
ancestry alone leaves ~500 dead branches behind. Classifying by **GitHub PR
state** recovers them. Nothing did that for local branches:

  * ``scripts/pi-reap-worktrees.sh`` (agent-infra #1095) reaps worktree
    CHECKOUTS only — it never calls ``git branch -D`` and deliberately retains
    the branch. It is reviewed, unscheduled, and its clean gate reclaims few.
  * ``scripts/scan-orphans.sh`` deletes a local branch only when it is recorded
    in ``~/.pi/agent/worktrees.jsonl`` (59 records vs ~1,473 branches).
  * ``scripts/cleanup-stale-branches.sh`` is remote-only.
  * ``scripts/cleanup-worktree.sh`` is single-branch post-merge ceremony.

This tool is the missing general local-branch reap path. It does NOT implement
worktree teardown — that policy is owned by the reviewed engine above, and a
second engine would diverge from it. On ``--apply --reap-worktrees`` it DELEGATES
the checkout-removal half to that engine, then re-reads the worktree list and
deletes only safe branches that are no longer checked out.

Policy contract
---------------
A local branch is deleted ONLY when its commits provably survive, checked in
this precedence (first match wins):

  1. trunk / ``--protect`` glob / the current branch / the main checkout's
     branch                      -> PRESERVE ``trunk``
  2. an open PR for this headRefName, or this branch is an OPEN PR's baseRefName
                                 -> PRESERVE ``open-pr`` / ``open-pr-base``
  3. tip is an ancestor of the main ref          -> SAFE ``ancestry``
  4. tip == the head SHA of a MERGED PR          -> SAFE ``pr-merged-tip``
                                                 (content landed per the PR record)
  5. tip == the head SHA of a CLOSED-unmerged PR -> PRESERVE ``pr-closed-tip``
                                                 by default; SAFE only with
                                                 ``--include-closed-unmerged``
  6. a PR exists but the tip differs             -> PRESERVE ``advanced-beyond-pr-tip``
  7. no PR at all, not an ancestor               -> JUDGEMENT (report only)
  8. a detached-HEAD worktree                    -> JUDGEMENT (report only)

The #4408 issue's name-only rule ("the branch's PR is merged") would delete the
branches in class 6 — a local tip that has ADVANCED beyond the PR head holds
commits that are not provably landed. Rule 4/5 compare the tip SHA, never the
name alone.

Closed-unmerged (class 5) is PRESERVE-by-default deliberately: the work never
landed, so the local ref may be the LAST non-revocable holder of its commits.
The fleet's own worktree engine made the same call (#1104: an only-``refs/heads/*``
holder survives; anything else is revocable). ``--include-closed-unmerged`` is
the explicit opt-in for an operator who has decided those PRs are dead.

Safety
------
* **Never ``--force``.** A dirty working tree (tracked or untracked) is reported
  and skipped; a branch held by any worktree keeps its checkout and its branch.
* **Truncated != clean.** The PR lists are fetched to completeness; a truncated
  or unqueryable surface makes the whole run INCOMPLETE and deletes NOTHING — so
  the ancestor rule can never fire on a branch whose open PR fell off a page.
* **TOCTOU.** Deletion uses the ATOMIC compare-and-delete primitive
  (``update-ref -d <ref> <expected-oid>``), so a branch that MOVED after
  classification is refused, never deleted. The checked-out set is RE-READ
  before the delete loop, so a worktree created during the (up to 900 s) bundle
  write still protects its branch. The PR surface itself is fetched ONCE, before
  that same window: a PR opened for an already-SAFE ancestor branch during it is
  not seen, so the open-PR veto is not applied and the ref is deleted. That race
  is inherent — PR state can change at any point — and is bounded: an ancestor
  branch's commits survive on ``main``, and the tip is in the recovery bundle.
  ``git update-ref -d refs/heads/<b> <classified-oid>``: it fails without
  deleting if the ref no longer equals the classified tip. ``update-ref`` does
  NOT itself refuse a branch checked out in a worktree, so the checked-out guard
  is a ``git worktree list`` snapshot taken INSIDE :func:`delete_branches` —
  after the backup bundle has been written and immediately before the delete
  loop, then refreshed every :data:`HELD_RECHECK_BATCH` deletes. It is NOT taken
  by the caller: the caller's snapshot precedes the recovery write, the report
  write, the disk probe and ``git bundle create`` (timeout 900 s), which is the
  window that let a worktree created during the bundle write be deleted out from
  under. A branch a worktree created inside the remaining one-batch window still
  holds is RESTORED by :func:`_reconcile_held_deletions` once the loop ends, so
  the net residual is zero (a branch cannot be checked out by a *new* worktree
  after its ref is gone).
* **Recovery.** A machine-readable recovery record (full tip SHAs) is written
  BEFORE the delete phase, independent of ``--report``; the report also carries a
  Recovery record section. Those SHAs are NOT durable by themselves: ``git
  update-ref -d`` removes the deleted ref's reflog, so a tip survives only until
  the objects are pruned (``gc.pruneExpire``, 2 weeks by default, immediately
  under ``gc --prune=now``). ``--backup-bundle`` writes one ``git bundle`` of the
  deleted tips and is what makes recovery durable; it is written by DEFAULT on
  ``--apply`` (``--no-backup`` opts out and leaves only the SHAs). A bundle that
  cannot be produced aborts the whole delete phase rather than deleting
  unbacked-up. The bundle stages its tips through temporary refs — no persistent
  ``refs/reaped/*`` refs, which would collide with the worktree engine's
  ``refs/heads/*``-only survival doctrine.
* **Dirty worktrees are reported, never force-removed.** A branch held by ANY
  worktree is preserved; the report marks whether that checkout is dirty.
* **Guard.** ``--apply`` refuses to run from the MAIN checkout: this tool invokes
  ref deletion from an interpreter file payload, which the
  ``main-worktree-guard`` extension does not content-gate, so the tool declares
  and enforces the run-from-a-worktree contract itself.
* Report and bundle writes refuse a symlinked target and replace atomically
  (``os.replace``), so a planted link cannot clobber another file.
* Remote branches are NEVER touched.
* **What a report actually covers.** A dry run lists every branch by class, and
  for worktrees it lists ONLY those holding a SAFE branch (the held table) or
  sitting on a DETACHED HEAD; a worktree holding a PRESERVE/JUDGEMENT branch is
  not listed individually. The delegated worktree engine
  (``pi-reap-worktrees.sh``) runs ONLY under ``--apply --reap-worktrees`` (always
  with ``--apply``), so a dry-run report has no delegate section at all — its
  output appears only on that apply path.

Exit codes (a delegate code is never passed through unmodified)
-------------------------------------------------------------
  0  complete (dry-run or apply)
  2  INCOMPLETE — a surface was truncated/unqueryable, the worktree engine was
     missing/unrunnable, or a filesystem surface was unwritable/unsearchable.
     Nothing was deleted. (`argparse` also exits 2 of its own accord on a syntax
     error, before this tool's own code path runs — so 2 can arrive without this
     tool having classified anything. See 3 for the tool's OWN usage refusal.)
  3  usage error raised by this tool, or ``--apply`` refused from the main checkout
  4  partial failure — at least one deletion was refused, or the worktree
     engine reported >=1 FAILED removal (the engine's exit code is remapped, not
     its output parsed)
  5  internal error (unexpected engine exit)
  6  INCOMPLETE AFTER DELETION — the run aborted after at least one branch may
     have been deleted: a mid-loop timeout, a FIRST target whose killed
     ``update-ref -d`` may already have landed (a deleted-or-unknown
     ``aborted``), a worktree-held branch that could not be restored, a report
     path that became a symlink between the pre-delete and post-delete writes,
     or a POST-delete report write that failed (unwritable, ENOSPC, EIO) — that
     last one is the case this code exists to keep from degrading into exit 1.
     Exit 2's "Nothing was deleted" contract does NOT hold here; the pre-delete
     recovery record holds every classified tip, so ``git branch <name> <sha>``
     restores any of them.

Usage
-----
    python3 tools/branch_reaper.py                      # dry-run, human report
    python3 tools/branch_reaper.py --json               # dry-run, machine report
    python3 tools/branch_reaper.py --report docs/runbook/4408-branch-reaper.md
    python3 tools/branch_reaper.py --apply              # delete safe branches only
    python3 tools/branch_reaper.py --apply --reap-worktrees
    python3 tools/branch_reaper.py --apply --include-closed-unmerged
"""
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_INCOMPLETE = 2
EXIT_USAGE = 3
EXIT_PARTIAL = 4
EXIT_INTERNAL = 5
#: Aborted AFTER >=1 deletion landed — the "nothing was deleted" contract of
#: EXIT_INCOMPLETE no longer holds, so automation gets a distinct signal.
EXIT_INCOMPLETE_AFTER_DELETE = 6


def _valid_slug(slug: str) -> bool:
    """True for exactly ``owner/name``.

    ``--slug`` is interpolated into the ``repos/{slug}/pulls`` API path, so a
    value carrying a space, ``?``, ``#`` or a newline builds a different request
    than the operator asked for.
    """
    if slug.count("/") != 1:
        return False
    owner, name = slug.split("/")
    # `.` and `..` are never GitHub owner/repo components, and they are the one
    # value in the allowed charset that changes which path `repos/{slug}/pulls`
    # resolves to — the exact thing this validator exists to prevent.
    if owner in (".", "..") or name in (".", ".."):
        return False
    ok = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.+"
    return bool(owner) and bool(name) and all(c in ok for c in owner + name)


#: Page caps. A list that reaches its cap is TRUNCATED, never "complete but short".
DEFAULT_MAX_PR_PAGES = 50
DEFAULT_PER_PAGE = 100

#: How often ``delete_branches`` re-reads the checked-out set. A full
#: ``git worktree list --porcelain`` over this repo's ~450 worktrees costs ~0.8 s
#: (measured), so a re-read *per delete* would add ~12 min to a 923-branch run;
#: one per batch keeps the checked-out guard at most ``batch`` ``update-ref``
#: calls old for ~30 s of that same run. The post-delete reconcile is what makes
#: the residual non-destructive, so this is a cost/latency choice, not the guard.
HELD_RECHECK_BATCH = 25

#: Worktree-engine (``pi-reap-worktrees.sh``) exit codes, remapped below.
_ENGINE_EXIT_OK = 0
_ENGINE_EXIT_USAGE = 2
_ENGINE_EXIT_FAILCLOSED = 3
_ENGINE_EXIT_PARTIAL = 4

VERDICT_SAFE = "SAFE"
VERDICT_PRESERVE = "PRESERVE"
VERDICT_JUDGEMENT = "JUDGEMENT"


class Incomplete(Exception):
    """A surface could not be enumerated to completeness. Delete nothing."""


# ── subprocess helpers ──────────────────────────────────────────────────────

def _run(cmd: list[str], *, cwd: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise Incomplete(
            f"command timed out after {timeout}s: {' '.join(cmd[:2])}") from exc
    except OSError as exc:
        # An ABSENT or non-executable binary is an UNQUERYABLE SURFACE, not a
        # crash: with no `gh` the PR-state gate cannot run at all, which is this
        # tool's documented INCOMPLETE (exit 2) — not an exit-1 traceback. The
        # in-file precedent is the worktree engine, which guards the same
        # "binary missing" class with `os.path.isfile` -> Incomplete.
        raise Incomplete(f"cannot execute {cmd[0]!r}: {exc}") from exc


def _gh_bin() -> str:
    return os.environ.get("BRANCH_REAPER_GH", "gh")


def _now() -> int:
    raw = os.environ.get("BRANCH_REAPER_NOW")
    if raw:
        try:
            return int(raw)
        except ValueError as exc:
            # A malformed clock is a bad input, not a crash: surface it as the
            # documented INCOMPLETE rather than a ValueError traceback.
            raise Incomplete(f"BRANCH_REAPER_NOW is not an integer: {raw!r}") from exc
    import time

    return int(time.time())


# ── repo / target resolution ────────────────────────────────────────────────

def resolve_repo(target: str | None) -> tuple[str, str | None]:
    """Return ``(repo_root, slug_or_None)`` for a path or ``owner/name`` target."""
    target = target or os.getcwd()
    if os.path.isdir(target):
        root = _run(["git", "-C", target, "rev-parse", "--show-toplevel"])
        if root.returncode != 0:
            raise ValueError(f"{target!r} is not inside a git repository")
        return os.path.realpath(root.stdout.strip()), _slug_from_remote(target)
    # Not a directory: treat as owner/name.
    if "/" in target:
        # The documented `--repo owner/name` form reaches the same `repos/{slug}/pulls`
        # API path as `--slug`, so it must clear the same validation.
        if not _valid_slug(target):
            raise ValueError(f"{target!r} is not a directory or 'owner/name'")
        return os.getcwd(), target
    raise ValueError(f"--repo {target!r} is not a directory or owner/name")


def _slug_from_remote(repo_root: str) -> str | None:
    remote = _run(["git", "-C", repo_root, "remote", "get-url", "origin"])
    if remote.returncode != 0:
        return None
    url = remote.stdout.strip()
    if url.endswith(".git"):
        url = url[:-4]
    if "github.com" in url:
        tail = url.split("github.com", 1)[1]
        # A ported URL (``ssh://git@github.com:22/owner/name``) leaves a LEADING
        # ``:22``; stripping ``:/`` would fold the port into the owner and yield
        # ``22/owner``. Strip a numeric port first, then the path separator.
        if tail.startswith(":") and tail[1:2].isdigit() and "/" in tail:
            # Everything before the first "/" is the port: ":22/o/n" -> "o/n".
            tail = tail.split("/", 1)[1]
        parts = [p for p in tail.lstrip(":/").split("/") if p]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return None


def default_branch(repo_root: str) -> str:
    ref = _run(["git", "-C", repo_root, "symbolic-ref", "refs/remotes/origin/HEAD"])
    if ref.returncode == 0 and ref.stdout.strip():
        return ref.stdout.strip().rsplit("/", 1)[-1]
    for candidate in ("main", "master"):
        if _run(["git", "-C", repo_root, "show-ref", "--verify", "--quiet",
                 f"refs/heads/{candidate}"]).returncode == 0:
            return candidate
    return "main"


def main_ref(repo_root: str) -> str:
    if _run(["git", "-C", repo_root, "show-ref", "--verify", "--quiet",
             "refs/remotes/origin/main"]).returncode == 0:
        return "origin/main"
    return default_branch(repo_root)


def is_main_checkout(repo_root: str) -> bool:
    """True when ``repo_root`` IS the main worktree (not a linked checkout).

    Resolved by identity, not a cwd-string compare: any subdirectory or symlinked
    spelling of the main checkout must still be refused.
    """
    top = _run(["git", "-C", repo_root, "rev-parse", "--show-toplevel"])
    if top.returncode != 0:
        return True
    toplevel = os.path.realpath(top.stdout.strip())
    listing = _run(["git", "-C", repo_root, "worktree", "list", "--porcelain"])
    for line in listing.stdout.splitlines():
        if line.startswith("worktree "):
            return os.path.realpath(line[len("worktree "):]) == toplevel
    return True


def current_branch(repo_root: str) -> str | None:
    """The checked-out branch name, unambiguous.

    ``--abbrev-ref`` applies the same shortest-UNAMBIGUOUS rule as
    ``%(refname:short)``: with a tag colliding with the branch it returns
    ``heads/release/1.0``, which then matches no ``enum_branches`` key and
    protects nothing — defeating the "current branch is PRESERVE" rule.
    ``--symbolic-full-name HEAD`` has no such ambiguity, so the prefix is
    stripped deterministically.
    """
    res = _run(["git", "-C", repo_root, "rev-parse", "--symbolic-full-name", "HEAD"])
    name = res.stdout.strip()
    if res.returncode != 0 or not name.startswith("refs/heads/"):
        return None
    return name[len("refs/heads/"):]


# ── enumeration ─────────────────────────────────────────────────────────────

def enum_branches(repo_root: str) -> dict[str, dict]:
    """name -> {oid, ts} for every local branch."""
    # ``lstrip=2``, NOT ``refname:short``: ``:short`` returns the shortest
    # UNAMBIGUOUS name across ALL refs, so a tag colliding with a branch yields a
    # prefix-qualified ``heads/release/1.0``. That name matches no PR (the
    # open-PR veto is skipped) and later builds ``refs/heads/heads/...``, which
    # resolves to nothing — every affected delete refuses. ``lstrip=2`` strips
    # exactly ``refs/heads/``.
    fmt = "%(refname:lstrip=2)%09%(objectname)%09%(committerdate:unix)"
    res = _run(["git", "-C", repo_root, "for-each-ref", f"--format={fmt}", "refs/heads"])
    if res.returncode != 0:
        raise Incomplete(f"git for-each-ref refs/heads failed: {res.stderr.strip()}")
    branches: dict[str, dict] = {}
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, oid, ts = parts
        branches[name] = {"oid": oid, "ts": int(ts)}
    return branches


def enum_ancestors(repo_root: str, ref: str) -> set[str]:
    """Branches whose tip is reachable from ``ref`` (ancestor test, one fork)."""
    res = _run(["git", "-C", repo_root, "for-each-ref", "--merged", ref,
                "--format=%(refname:lstrip=2)", "refs/heads"])
    if res.returncode != 0:
        raise Incomplete(f"git for-each-ref --merged {ref} failed: {res.stderr.strip()}")
    return {ln.strip() for ln in res.stdout.splitlines() if ln.strip()}


def enum_worktrees(repo_root: str) -> list[dict]:
    """Parse ``git worktree list --porcelain`` into records."""
    res = _run(["git", "-C", repo_root, "worktree", "list", "--porcelain"])
    if res.returncode != 0:
        raise Incomplete(f"git worktree list failed: {res.stderr.strip()}")
    records: list[dict] = []
    cur: dict | None = None
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            if cur:
                records.append(cur)
            cur = {"path": line[len("worktree "):], "branch": None, "head": None,
                   "detached": False, "bare": False, "locked": False, "prunable": False}
        elif cur is None:
            continue
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch refs/heads/"):
            cur["branch"] = line[len("branch refs/heads/"):]
        elif line == "detached":
            cur["detached"] = True
        elif line == "bare":
            cur["bare"] = True
        elif line.startswith("locked"):
            cur["locked"] = True
        elif line.startswith("prunable"):
            cur["prunable"] = True
    if cur:
        records.append(cur)
    return records


def worktree_dirty(path: str) -> bool:
    """Tracked-modified or untracked => dirty. Ignored-only is NOT dirty.

    ``git worktree remove`` does not refuse ignored files, so treating a
    reproducible ``.venv`` as dirt would make every worktree unreapable; a
    tracked/untracked change is the class that can hold unlanded work.
    """
    res = _run(["git", "-C", path, "status", "--porcelain"])
    if res.returncode != 0:
        return True  # cannot evaluate => treat as dirty (fail closed)
    return bool(res.stdout.strip())


def _pr_ref(pr: dict, key: str, sub: str = "ref") -> str | None:
    """Read ``pr[key][sub]`` defensively, fail-closed.

    ``head`` and ``base`` are nested objects in the GitHub payload. A malformed
    response carrying a string there makes ``.get()`` raise AttributeError, which
    would escape as an internal fault instead of the documented Incomplete — and
    skipping a PR whose ``head`` cannot be read would silently drop the open-PR
    veto, which is fail-OPEN. So an unreadable shape is an abort, not a skip.
    """
    obj = pr.get(key)
    if obj is None:
        return None
    if not isinstance(obj, dict):
        raise Incomplete(
            f"gh returned a non-object '{key}' on PR {pr.get('number')}")
    value = obj.get(sub)
    if value is not None and not isinstance(value, str):
        raise Incomplete(
            f"gh returned a non-string '{key}.{sub}' on PR {pr.get('number')}")
    return value


def fetch_prs(slug: str, *, max_pages: int, per_page: int) -> dict:
    """Enumerate every PR (open + closed) via REST to completeness.

    The GraphQL ``gh pr list --state closed`` path resets on this host
    (``collision_preflight.py`` #3587); REST ``--paginate --slurp`` is used.
    Reaching ``max_pages`` is TRUNCATED -> INCOMPLETE, never "complete but short".
    """
    if not slug:
        raise Incomplete("no GitHub repo slug — pass --slug owner/name for a repo without a github.com origin")

    def _fetch(state: str) -> list[list[dict]]:
        cmd = [_gh_bin(), "api", "--paginate", "--slurp",
               f"repos/{slug}/pulls?state={state}&per_page={per_page}"]
        res = _run(cmd, timeout=300)
        if res.returncode != 0:
            raise Incomplete(
                f"gh pr enumeration ({state}) failed: {res.stderr.strip() or 'non-zero exit'}")
        if not res.stdout.strip():
            # An exit-0 EMPTY body is not "zero PRs". A valid `--paginate --slurp`
            # payload is never empty (an empty page is `[[]]`), so this branch
            # would silently drop EVERY PR surface — every open-PR veto and every
            # open-base veto — and let ancestry delete an open-PR branch at exit
            # 0. That is the same fail-open as a malformed record, one level up.
            raise Incomplete(
                f"gh pr enumeration ({state}) returned an empty body with exit 0 — "
                f"an empty slurp payload is never valid, and treating it as no PRs "
                f"would drop every veto")
        try:
            pages = json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            raise Incomplete(f"gh pr enumeration ({state}) returned malformed JSON: {exc}") from exc
        if not isinstance(pages, list):
            raise Incomplete(f"gh pr enumeration ({state}) returned {type(pages).__name__}, expected list")
        if not pages:
            # A bare `[]` is likewise never a valid slurp body.
            raise Incomplete(
                f"gh pr enumeration ({state}) returned an empty page list — "
                f"a valid slurp payload always has at least one page")
        if len(pages) >= max_pages:
            raise Incomplete(
                f"gh pr enumeration ({state}) hit the {max_pages}-page cap — TRUNCATED")
        return pages

    open_pages = _fetch("open")
    closed_pages = _fetch("closed")

    open_by_head: dict[str, list[dict]] = {}
    merged_by_head: dict[str, list[dict]] = {}
    closed_unmerged_by_head: dict[str, list[dict]] = {}
    open_bases: set[str] = set()
    for pages, dest in ((open_pages, open_by_head), (closed_pages, None)):
        for page in pages:
            if not isinstance(page, list):
                raise Incomplete(
                    f"gh returned a non-list page ({type(page).__name__})")
            for pr in page:
                if not isinstance(pr, dict):
                    raise Incomplete(
                        f"gh returned a non-object PR record ({type(pr).__name__})")
                head = _pr_ref(pr, "head")
                if not head:
                    # Fail CLOSED. `continue` here would silently drop the PR, and
                    # an open PR that cannot be attributed to a branch is exactly
                    # the veto that stops that branch being deleted by ancestry —
                    # a malformed record must abort the run, not skip the check.
                    raise Incomplete(
                        f"gh returned a PR with no head ref (PR {pr.get('number')})")
                row = {"number": pr.get("number"),
                       "sha": _pr_ref(pr, "head", "sha"),
                       "merged": pr.get("merged_at") is not None}
                if dest is not None:
                    dest.setdefault(head, []).append(row)
                    base = _pr_ref(pr, "base")
                    if not base:
                        # An OPEN PR always has a base; without it the base veto
                        # (a live integration target) cannot be applied at all.
                        raise Incomplete(
                            f"gh returned an OPEN PR with no base ref "
                            f"(PR {pr.get('number')})")
                    # An OPEN PR's base is a live integration target: deleting
                    # that local branch would break the PR (live shape: PR
                    # #4181's base is the local branch feat/2409-contact-form).
                    open_bases.add(base)
                elif row["merged"]:
                    merged_by_head.setdefault(head, []).append(row)
                else:
                    closed_unmerged_by_head.setdefault(head, []).append(row)
    return {"open": open_by_head, "merged": merged_by_head,
            "closed_unmerged": closed_unmerged_by_head,
            "open_bases": open_bases}


# ── classification (pure — the unit under mutation test) ────────────────────

def classify(
    branches: dict[str, dict],
    worktrees: list[dict],
    prs: dict,
    ancestors: set[str],
    *,
    protected: set[str],
    include_closed_unmerged: bool,
) -> list[dict]:
    """Return one classified row per local branch. Pure function."""
    held: dict[str, str] = {}
    for wt in worktrees:
        if wt.get("branch"):
            held.setdefault(wt["branch"], wt["path"])

    rows: list[dict] = []
    for name, info in sorted(branches.items()):
        oid, ts = info["oid"], info["ts"]
        row = {"branch": name, "oid": oid, "ts": ts,
               "verdict": None, "reason": None, "commits_survive": None,
               "pr_number": None, "worktree": held.get(name)}
        merged = prs["merged"].get(name, [])
        closed = prs["closed_unmerged"].get(name, [])
        if name in protected:
            row["verdict"], row["reason"] = VERDICT_PRESERVE, "trunk"
        elif name in prs["open"]:
            row["verdict"], row["reason"] = VERDICT_PRESERVE, "open-pr"
            row["pr_number"] = prs["open"][name][0]["number"]
        elif name in prs["open_bases"]:
            # `prs["open_bases"]` not `.get(...)`: a fail-open default here would
            # silently disable the base veto if `fetch_prs` ever stopped emitting
            # the key, and this is a delete-decision input.
            # This branch is the BASE of an open PR. Deleting it would break a
            # live PR even when the branch itself is an ancestor of the main ref
            # (rule 3 would otherwise call it SAFE). Rule 2 checked only the PR
            # HEAD; this is the base-ref veto.
            row["verdict"], row["reason"] = VERDICT_PRESERVE, "open-pr-base"
        elif name in ancestors:
            row["verdict"], row["reason"] = VERDICT_SAFE, "ancestry"
            row["commits_survive"] = "reachable-from-main"
        elif merged and any(pr["sha"] == oid for pr in merged):
            row["verdict"], row["reason"] = VERDICT_SAFE, "pr-merged-tip"
            row["commits_survive"] = "merged-pr-record"
            row["pr_number"] = next(pr["number"] for pr in merged if pr["sha"] == oid)
        elif closed and any(pr["sha"] == oid for pr in closed):
            row["pr_number"] = next(pr["number"] for pr in closed if pr["sha"] == oid)
            if include_closed_unmerged:
                row["verdict"], row["reason"] = VERDICT_SAFE, "pr-closed-tip"
                row["commits_survive"] = "closed-pr-record"
            else:
                row["verdict"], row["reason"] = VERDICT_PRESERVE, "pr-closed-tip"
        elif merged or closed:
            row["verdict"], row["reason"] = VERDICT_PRESERVE, "advanced-beyond-pr-tip"
            pr = (merged or closed)[0]
            row["pr_number"] = pr["number"]
        else:
            row["verdict"], row["reason"] = VERDICT_JUDGEMENT, "no-pr-non-ancestor"
        rows.append(row)
    return rows


def detached_worktrees(worktrees: list[dict]) -> list[dict]:
    return [wt for wt in worktrees if wt.get("detached") and not wt.get("bare")]


def _detached_head_ts(repo_root: str, worktrees: list[dict]) -> dict[str, int]:
    """HEAD commit time for each detached worktree (so the report ranks by age)."""
    out: dict[str, int] = {}
    for wt in detached_worktrees(worktrees):
        head = wt.get("head")
        if not head:
            continue
        res = _run(["git", "-C", repo_root, "show", "-s", "--format=%ct", head], timeout=30)
        if res.returncode == 0 and res.stdout.strip().isdigit():
            out[head] = int(res.stdout.strip())
    return out


def _reject_dotdot(path: str) -> None:
    """Refuse a path containing a ``..`` component.

    ``os.path.abspath`` collapses ``..`` LEXICALLY, but the OS resolves
    ``symlink/..`` against the symlink's TARGET. So ``repo/docs/../x.md`` (with
    ``docs -> /outside``) normalizes to ``repo/x.md``, walks clean, and then
    escapes through the link — an undeclared hole in `_under_repo_symlink`.
    Rejecting the component is the only way a string-level check can be safe.
    """
    if ".." in Path(path).parts:
        raise Incomplete(f"refusing a path with a '..' component: {path}")


def _under_repo_symlink(p: Path, repo_root: str | None) -> Path | None:
    """The first symlinked component of ``p``'s parents that lies INSIDE the repo.

    A checkout can plant ``docs/runbook -> /etc``, and ``mkstemp(dir=parent)``
    plus ``os.replace`` would then write through it into the target — so a
    repo-controlled symlinked component is refused. Components OUTSIDE the repo
    are not: macOS makes ``/tmp`` itself a symlink to ``/private/tmp``, and
    refusing every symlinked ancestor would refuse ordinary temp paths.

    Both sides compare UNRESOLVED (``abspath``). Resolving them would erase the
    very symlink this is looking for, and ``realpath``-ing the repo would also
    resolve macOS's own ``/var -> /private/var``, breaking the prefix match.

    Callers MUST have rejected a ``..`` component first (``_reject_dotdot``):
    ``abspath`` collapses ``..`` lexically while the OS resolves ``symlink/..``
    against the link's target, so a ``..`` walk cannot be made safe here.

    KNOWN RESIDUAL (fail-open): the check is a prefix match, so it only fires
    when the caller's path and ``repo_root`` are spelled in the SAME alias. If
    an operator passes a ``--report`` path under an unresolved alias of the repo
    (``/var/...`` while ``resolve_repo`` returned ``/private/var/...``) a planted
    component is not detected. ``repo_root=None`` also allows by construction.
    Closing this needs an openat/O_NOFOLLOW component walk, not a string prefix.
    """
    if repo_root is None:
        return None
    # BOTH sides unresolved, deliberately. `realpath`-ing the repo would resolve
    # macOS's own `/var -> /private/var` (and `/tmp -> /private/tmp`) and the
    # prefix match against an operator-supplied path would then fail — silently
    # failing OPEN on exactly the plant this guards.
    root = Path(os.path.abspath(repo_root))
    parent = Path(os.path.abspath(str(p.parent)))
    try:
        rel = parent.relative_to(root)
    except ValueError:
        return None
    walk = root
    for part in rel.parts:
        walk = walk / part
        if walk.is_symlink():
            return walk
    return None


def _write_text_safe(path: str, text: str, repo_root: str | None = None) -> None:
    """Write atomically, refusing to follow a symlink at ``path`` (#4098 class).

    ``Path.write_text`` follows a symlink; a planted link at a documented report
    path would clobber its target. A same-directory temp file plus ``os.replace``
    replaces the link itself, never its target.
    """
    p = Path(path)
    _reject_dotdot(path)
    # The leaf always; and a symlinked PARENT only when the repo itself controls
    # it (see `_under_repo_symlink`) — the naive "any symlinked ancestor" rule
    # refuses `/tmp/report.md` on macOS, where `/tmp` IS a symlink.
    if p.is_symlink():
        raise Incomplete(f"refusing to write through a symlink: {path}")
    planted = _under_repo_symlink(p, repo_root)
    if planted is not None:
        raise Incomplete(
            f"refusing to write through a symlinked directory inside the "
            f"repository: {planted}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=p.name + ".tmp.", dir=str(p.parent))
    except OSError as exc:
        raise Incomplete(f"cannot write {path}: {exc}") from exc
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, p)
    except OSError as exc:
        # An unwritable surface (EACCES/ENOSPC/EROFS/EIO) is the documented
        # INCOMPLETE, not a traceback. This matters most AFTER a delete phase:
        # the post-delete report write is the exit-6 signal, and an OSError
        # escaping here used to lose it.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise Incomplete(f"cannot write {path}: {exc}") from exc
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _write_json_safe(path: str, payload: dict, repo_root: str | None = None) -> None:
    _write_text_safe(path, json.dumps(payload, indent=2) + "\n", repo_root)


def _make_backup_bundle(repo_root: str, path: str, targets: list[dict]) -> None:
    """Write ONE bundle of the given tips, or raise Incomplete (fail closed).

    ``git bundle create <file> <sha>`` is rejected ("Refusing to create empty
    bundle") because a bundle records REF names, not bare commits — so temporary
    refs under a UNIQUE per-process namespace are created for the tips, the
    bundle is written, its heads are verified to be exactly the expected tips,
    and the refs are removed immediately. They are deliberately NOT the
    persistent ``refs/reaped/*`` shape, which would collide with
    ``pi-reap-worktrees.sh``'s ``refs/heads/*``-only survival doctrine.

    Symlink-safe and atomic: ``git bundle create`` follows a symlink and truncates
    its target, so a pre-check alone is not enough — the bundle is written to a
    private temp file in the destination directory and ``os.replace``d into place
    (which replaces a planted link, never its target). A relative ``path`` is
    resolved against ``repo_root``, the same directory ``git -C repo_root`` uses,
    so the check and the write agree.
    """
    dest = Path(path)
    _reject_dotdot(path)
    if not dest.is_absolute():
        dest = Path(repo_root) / dest
    if dest.is_symlink():
        raise Incomplete(f"refusing to write a symlinked bundle path: {dest}")
    planted = _under_repo_symlink(dest, repo_root)
    if planted is not None:
        raise Incomplete(
            f"refusing to write a bundle through a symlinked directory inside "
            f"the repository: {planted}")
    if dest.exists():
        # The bundle is the DURABLE artifact — `os.replace` over an existing one
        # would discard the only durable copy of a previous run's tips.
        raise Incomplete(f"refusing to overwrite an existing backup bundle: {dest}")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise Incomplete(f"cannot create bundle directory {dest.parent}: {exc}") from exc
    try:
        fd, tmp = tempfile.mkstemp(prefix=dest.name + ".tmp.", dir=str(dest.parent))
        os.close(fd)
        os.unlink(tmp)  # git bundle create must create the file itself
    except OSError as exc:
        raise Incomplete(f"cannot prepare bundle path {dest}: {exc}") from exc
    prefix = f"refs/branch-reaper-backup/{os.getpid()}-{os.urandom(4).hex()}"
    refs: list[str] = []
    try:
        for i, r in enumerate(targets):
            ref = f"{prefix}/{i:06d}"
            upd = _run(["git", "-C", repo_root, "update-ref", ref, r["oid"]])
            if upd.returncode != 0:
                raise Incomplete(f"could not stage backup ref {ref}: {upd.stderr.strip()}")
            refs.append(ref)
        if not refs:
            return
        res = _run(["git", "-C", repo_root, "bundle", "create", tmp, *refs], timeout=900)
        if res.returncode != 0:
            raise Incomplete(f"git bundle create failed: {res.stderr.strip()}")
        heads = _run(["git", "-C", repo_root, "bundle", "list-heads", tmp], timeout=120)
        got = {ln.split()[0] for ln in heads.stdout.splitlines() if ln.strip()}
        expected = {r["oid"] for r in targets}
        if got != expected:
            raise Incomplete("backup bundle does not expose the expected tips")
        try:
            os.replace(tmp, dest)
        except OSError as exc:
            raise Incomplete(f"cannot move bundle into place at {dest}: {exc}") from exc
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        # Cleanup must never raise (a timeout here must not replace the real
        # error) — a stranded temp ref only makes the worktree engine PRESERVE.
        for ref in refs:
            with contextlib.suppress(Exception):
                _run(["git", "-C", repo_root, "update-ref", "-d", ref], timeout=60)


# ── reporting ───────────────────────────────────────────────────────────────

#: True once ANY branch may have been deleted. Read by the TOP-LEVEL handler in
#: `main`, so the after-deletion distinction is a property of the BOUNDARY rather
#: than of each call-site tuple. It lived in two call-site handlers for three
#: review rounds, and each round found one more path that reached the boundary and
#: returned exit 2 — whose documented meaning, "Nothing was deleted", was by then
#: false (a broken pipe on the post-delete output block; a post-apply
#: `build_report`; a clock that raised a type the handlers did not catch).
_LANDED = False


def _mark_landed(results: list[dict] | None) -> None:
    """Record that deletions landed, for the top-level handler to consult."""
    global _LANDED
    if _landed_results(results):
        _LANDED = True


def _fmt_ts(ts: int) -> str:
    import datetime

    try:
        return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        # A REPORTING date, never a decision input, and it raises THREE types
        # across the out-of-range-clock surface: OSError (the middle band),
        # ValueError (year > 9999) and OverflowError (ts >= 2**63). A bad clock
        # must not abort a run that has already classified — or, worse, already
        # deleted (that path lost the exit-6 signal and returned an undocumented 1).
        return f"(unrepresentable: {ts})"


def _md(value) -> str:
    """Escape an untrusted, git-derived string for a Markdown table cell.

    A refname may legally contain a backtick and ``|`` (``git check-ref-format``
    accepts both), so a crafted branch name would otherwise break the row's cell
    boundary and the surrounding code span of a report that gets committed and
    rendered. HTML-encoding the backtick keeps it visible without closing a span.
    """
    s = "" if value is None else str(value)
    return s.replace("&", "&amp;").replace("|", "\\|").replace("`", "&#96;")


def build_report(rows: list[dict], worktrees: list[dict], ancestors: set[str],
                 *, repo_root: str, slug: str | None, main_ref_used: str,
                 include_closed_unmerged: bool, engine_output: str | None = None,
                 apply_results: list[dict] | None = None, now: int | None = None,
                 disk: dict | None = None, recovery: list[dict] | None = None,
                 detached_ts: dict[str, int] | None = None) -> str:
    safe = [r for r in rows if r["verdict"] == VERDICT_SAFE]
    held_safe = [r for r in safe if r["worktree"]]
    deletable = [r for r in safe if not r["worktree"]]
    preserve = [r for r in rows if r["verdict"] == VERDICT_PRESERVE]
    judgement = sorted((r for r in rows if r["verdict"] == VERDICT_JUDGEMENT), key=lambda r: r["ts"])
    detached = detached_worktrees(worktrees)
    now = now or _now()

    by_reason: dict[str, int] = {}
    for r in rows:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1

    out: list[str] = []
    out.append("---")
    out.append('title: "4408 — Worktree/branch reaper dry-run report"')
    out.append("type: operations")
    out.append("domain: operations")
    out.append("doc_status: live")
    out.append("created: 2026-09-20")
    out.append("ownedBy: organisation-design-team")
    out.append("aboutSubjects: organisation-design-team")
    out.append("aboutObjects: tortoise")
    out.append("---")
    out.append("")
    out.append("# branch-reaper dry-run report — #4408\n")
    out.append(f"Generated: {_fmt_ts(now)} · repo `{repo_root}` · slug `{slug or 'n/a'}` · "
               f"main ref `{main_ref_used}`\n")
    out.append("Tool: `tools/branch_reaper.py`. Dry-run by default; this report is the "
               "evidence a reviewer reads before any `--apply`.\n")
    out.append(f"`--include-closed-unmerged`: **{include_closed_unmerged}**\n")
    out.append("## Summary\n")
    out.append("| metric | count |")
    out.append("|---|---|")
    out.append(f"| local branches | {len(rows)} |")
    out.append(f"| **SAFE_BY_HISTORY** | **{len(safe)}** |")
    out.append(f"| of which checked out in a worktree | {len(held_safe)} |")
    out.append(f"| **SAFE_DELETABLE_now** | **{len(deletable)}** |")
    out.append(f"| PRESERVE | {len(preserve)} |")
    out.append(f"| JUDGEMENT (report only) | {len(judgement)} |")
    out.append(f"| worktrees | {len(worktrees)} (detached: {len(detached)}) |")
    out.append("")
    out.append("### Verdicts by reason\n")
    out.append("| reason | count |")
    out.append("|---|---|")
    for reason in sorted(by_reason):
        out.append(f"| {reason} | {by_reason[reason]} |")
    out.append("")

    out.append("## Safe — deletable now\n")
    out.append("Branch tip provably survives and no worktree holds it.\n")
    out.append("| branch | tip | age (days) | verdict | survives | PR |")
    out.append("|---|---|---|---|---|---|")
    for r in sorted(deletable, key=lambda r: r["ts"]):
        age = (now - r["ts"]) // 86400
        out.append(f"| `{_md(r['branch'])}` | `{r['oid'][:12]}` | {age} | {r['reason']} | "
                   f"{r['commits_survive']} | {r['pr_number'] or '—'} |")
    out.append("")

    if held_safe:
        out.append("## Safe by history — held by a worktree (branch preserved)\n")
        out.append("The delegate preserves these checkouts (dirty / ignored-artifact / too-recent), "
                   "or they were not torn down; the branch is kept. Reported, not deleted.\n")
        out.append("| branch | worktree | dirty | verdict | PR |")
        out.append("|---|---|---|---|---|")
        for r in sorted(held_safe, key=lambda r: r["ts"]):
            dirty = "yes" if r.get("dirty") else "no"
            out.append(f"| `{_md(r['branch'])}` | `{_md(r['worktree'])}` | {dirty} | "
                       f"{r['reason']} | {r['pr_number'] or '—'} |")
        out.append("")

    out.append("## Judgement — no PR, not an ancestor (never auto-deleted)\n")
    out.append("Oldest first. A human decides.\n")
    out.append("| branch | tip | age (days) |")
    out.append("|---|---|---|")
    for r in judgement:
        age = (now - r["ts"]) // 86400
        out.append(f"| `{_md(r['branch'])}` | `{r['oid'][:12]}` | {age} |")
    out.append("")

    if detached:
        out.append("## Detached-HEAD worktrees (never auto-deleted)\n")
        out.append("Oldest HEAD first. A human decides.\n")
        out.append("| path | HEAD | age (days) |")
        out.append("|---|---|---|")
        ts_map = detached_ts or {}
        for wt in sorted(detached, key=lambda w: ts_map.get(w.get("head") or "", 1 << 62)):
            ts = ts_map.get(wt.get("head") or "")
            age = "—" if ts is None else str((now - ts) // 86400)
            out.append(f"| `{_md(wt['path'])}` | `{(wt.get('head') or '?')[:12]}` | {age} |")
        out.append("")

    if recovery is not None:
        out.append("## Recovery record (written before deletion)\n")
        out.append("Full tip SHAs; `git branch <name> <sha>` restores a branch. These SHAs are "
                   "**not** durable on their own — `git update-ref -d` removes the deleted ref's "
                   "reflog, so a SHA restores a branch only until the objects are pruned "
                   "(`gc.pruneExpire`, 2 weeks by default, immediately under `gc --prune=now`). "
                   "The backup bundle written by `--apply` is the durable copy; without it "
                   "(`--no-backup`) these tips can become unrecoverable.\n")
        out.append("| branch | tip | verdict | PR |")
        out.append("|---|---|---|---|")
        for r in recovery:
            out.append(f"| `{_md(r['branch'])}` | `{r['oid']}` | {r.get('verdict', '')} | "
                       f"{r.get('pr_number') or '—'} |")
        out.append("")

    if engine_output is not None:
        out.append("## Delegated worktree engine (`pi-reap-worktrees.sh`)\n")
        out.append("```")
        # The engine's own output is untrusted text; a fence wider than any run of
        # backticks in it cannot be closed early by the payload. `out[-1]` is the
        # opening fence just appended.
        payload = engine_output.strip()[-4000:]
        fence = "`" * max(3, max((len(m) for m in payload.split("\n")), default=0) + 1)
        out[-1] = fence
        out.append(payload)
        out.append(fence)
        out.append("")

    if apply_results is not None:
        deleted = [r for r in apply_results if r["result"] == "deleted"]
        refused = [r for r in apply_results if r["result"] == "refused"]
        skipped = [r for r in apply_results if r["result"] == "skipped"]
        restored = [r for r in apply_results if r["result"] == "restored"]
        unknown = [r for r in apply_results if r["result"] in ("aborted", "unrestored")]
        out.append("## Post-apply results\n")
        out.append("| metric | value |")
        out.append("|---|---|")
        out.append(f"| deleted | {len(deleted)} |")
        out.append(f"| restored (a worktree created in the delete window held it) | {len(restored)} |")
        out.append(f"| refused | {len(refused)} |")
        out.append(f"| skipped (held / moved) | {len(skipped)} |")
        if unknown:
            out.append(f"| **aborted / unrestored — deleted-or-unknown** | **{len(unknown)}** |")
        if disk:
            out.append(f"| filesystem free before | {disk.get('before_kb')} KiB |")
            out.append(f"| filesystem free after | {disk.get('after_kb')} KiB |")
            out.append(f"| free-space delta | {disk.get('delta_kb')} KiB |")
        out.append("")
        out.append("### Deleted\n")
        out.append("| branch | tip |")
        out.append("|---|---|")
        for r in deleted:
            out.append(f"| `{_md(r['branch'])}` | `{r['oid']}` |")
        out.append("")
        if restored:
            out.append("### Restored — deleted while a worktree created in the window held it\n")
            out.append("The ref was deleted and then restored by the post-delete reconcile; "
                       "the branch is intact.\n")
            for r in restored:
                out.append(f"- `{_md(r['branch'])}` — {r.get('detail', 'restored')}")
            out.append("")
        if refused:
            out.append("### Refused\n")
            for r in refused:
                out.append(f"- `{_md(r['branch'])}` — {r.get('detail', 'refused')}")
            out.append("")
        if unknown:
            out.append("### Aborted / unrestored — deleted-or-unknown\n")
            for r in unknown:
                out.append(f"- `{_md(r['branch'])}` — {r.get('detail', r['result'])}")
            out.append("")
    return "\n".join(out) + "\n"


# ── apply ───────────────────────────────────────────────────────────────────

def run_worktree_engine(engine: str, repo_root: str, timeout: int) -> tuple[int, str]:
    if not os.path.isfile(engine):
        raise Incomplete(f"worktree engine not found: {engine} "
                         f"(set --worktree-engine or AGENT_INFRA_PATH)")
    res = _run(["bash", engine, "--apply", "--repo", repo_root], timeout=timeout)
    output = (res.stdout or "") + (("\n" + res.stderr) if res.stderr else "")
    return res.returncode, output


def _held_branches(repo_root: str) -> set[str]:
    """Local branch names checked out in ANY worktree (the main one included).

    A worktree whose branch ref has already been deleted still reports ``branch
    refs/heads/<name>`` — git reads the worktree HEAD symref and reports an
    all-zero HEAD — so this sees a held-but-deleted branch. That is what makes
    the post-delete reconcile possible.
    """
    return {wt["branch"] for wt in enum_worktrees(repo_root) if wt.get("branch")}


def _reconcile_held_deletions(repo_root: str, results: list[dict]) -> None:
    """Restore any branch this run deleted while a worktree still held it.

    ``git update-ref -d`` does NOT refuse a branch checked out in a worktree, so
    a worktree created between the ``held`` re-read and the delete of its branch
    can be left on a deleted ref. That window is CLOSED here: ``git worktree add
    <path> <branch>`` needs the branch ref to exist, so a worktree created AFTER
    our delete cannot name the deleted branch — a single re-read once the loop is
    over therefore sees every branch held by a worktree created while we ran.
    Each one is restored from the classified tip with the all-zero old-oid
    precondition (create-only), so a ref a concurrent lane has already re-created
    is never clobbered. A restore that fails is raised as ``Incomplete`` so the
    caller reports exit 6 and names the branch.
    """
    candidates = {r["branch"]: r for r in results if r["result"] in ("deleted", "aborted")}
    if not candidates:
        return
    for branch in sorted(_held_branches(repo_root) & set(candidates)):
        row = candidates[branch]
        if row["result"] == "aborted":
            # The killed update-ref may OR may not have landed; only a branch whose
            # ref is actually gone needs restoring (an existing ref is not broken).
            probe = _run(["git", "-C", repo_root, "rev-parse", "--verify", "--quiet",
                          f"refs/heads/{branch}"])
            if probe.returncode == 0 and probe.stdout.strip():
                continue
        # The all-zero OID is the create-only sentinel and its WIDTH is
        # repo-format dependent: a SHA-256 repo rejects a 40-char value ("not a
        # valid old SHA1"), so the restore would fail and the branch would stay
        # deleted under a live checkout.
        fmt_res = _run(["git", "-C", repo_root, "rev-parse", "--show-object-format"])
        width = 64 if fmt_res.returncode == 0 and fmt_res.stdout.strip() == "sha256" else 40
        res = _run(["git", "-C", repo_root, "update-ref",
                    f"refs/heads/{branch}", row["oid"], "0" * width])
        if res.returncode == 0:
            row["result"] = "restored"
            row["detail"] = ("deleted while a worktree created during the delete "
                             "window held it — branch restored from the classified tip")
        else:
            row["result"] = "unrestored"
            row["detail"] = ("REF DELETED WHILE HELD and the automatic restore failed: "
                             + (res.stderr or res.stdout).strip())
    stuck = sorted(r["branch"] for r in results if r["result"] == "unrestored")
    if stuck:
        raise Incomplete(
            "a worktree holds a branch this run deleted and it could not be restored: "
            + ", ".join(stuck))


#: Delete-phase outcomes where the ref may no longer exist. ``aborted`` is a
#: killed ``update-ref -d`` whose result the post-kill probe could not determine.
_LANDED_RESULTS = ("deleted", "aborted", "unrestored")


def _landed_results(results: list[dict] | None) -> list[dict]:
    """Rows whose ref may be gone — the input to the exit-6 decision.

    Keyed on more than ``deleted``: a killed ``update-ref -d`` is ``aborted``
    (deletion-or-unknown), so an aborted FIRST target must not read as "nothing
    was deleted".
    """
    return [r for r in (results or []) if r["result"] in _LANDED_RESULTS]


def delete_branches(repo_root: str, rows: list[dict],
                    *, backup_bundle: str | None,
                    results: list[dict] | None = None,
                    batch: int = HELD_RECHECK_BATCH) -> list[dict]:
    """Delete the SAFE rows, guarding the checked-out set HERE — not in the caller.

    Deletion uses the ATOMIC compare-and-delete primitive
    ``git update-ref -d refs/heads/<b> <expected>``: it fails without deleting
    if the ref no longer equals the classified tip, closing the
    check-then-act window. Because ``update-ref`` does NOT itself refuse a branch
    checked out in some worktree, a fresh ``git worktree list`` snapshot IS the
    checked-out guard, and it is taken INSIDE this function: once after
    ``_make_backup_bundle`` (whose ``git bundle create`` has a 900 s timeout —
    taking the snapshot before it was the P1 defect: a worktree created during
    the bundle write was invisible to the delete) and then every ``batch``
    deletes. The guard is therefore at most ``batch`` ``update-ref`` calls old,
    never as old as the slowest preceding operation. The residual — a worktree
    created between a re-read and its branch's delete — is closed by
    :func:`_reconcile_held_deletions`, which restores any deleted branch a
    worktree is found holding once the loop ends.

    ``results`` may be a caller-owned sink: when supplied, progress is appended
    to it as it happens, so an ``Incomplete`` raised mid-loop still leaves the
    caller the deletions that already landed (the caller uses that to return the
    distinct exit 6 instead of the "nothing was deleted" exit 2).
    """
    if results is None:
        results = []
    batch = max(1, batch)
    targets = [r for r in rows if r["verdict"] == VERDICT_SAFE]
    if backup_bundle:
        # ALL targets, not just the currently-unheld ones. A branch that is held
        # at THIS instant can have its worktree released while the (up to 900 s)
        # `git bundle create` runs; the delete phase re-reads held branches, so
        # that branch is then deleted while absent from the bundle. Bundling a
        # branch that ends up preserved is harmless — missing one that gets
        # deleted is the case the bundle exists to prevent.
        _make_backup_bundle(repo_root, backup_bundle, targets)
    held: set[str] = set()
    try:
        for i, r in enumerate(targets):
            b, expected = r["branch"], r["oid"]
            # The checked-out guard: re-read immediately before the FIRST delete
            # (after the bundle) and every `batch` deletes after that.
            if i % batch == 0:
                held = _held_branches(repo_root)
            if b in held:
                results.append({"branch": b, "oid": expected, "result": "skipped",
                                "detail": "worktree-held"})
                continue
            # Defensive: an empty/all-zero expected OID is NOT a valid compare-and-
            # delete sentinel — git treats all-zeros as "no old value" and deletes
            # unconditionally, so guard before it can reach update-ref.
            if not expected or set(expected) == {"0"}:
                results.append({"branch": b, "oid": expected, "result": "refused",
                                "detail": "missing classified OID"})
                continue
            try:
                res = _run(["git", "-C", repo_root, "update-ref", "-d",
                            f"refs/heads/{b}", expected])
            except Incomplete as exc:
                # A killed update-ref may already have landed: probe the ref so the
                # caller can tell "may have deleted" from "deleted nothing" (an
                # aborted-FIRST run must NOT read as the "nothing was deleted"
                # exit 2). If the probe is itself unavailable the row stays
                # `aborted` — deletion-or-unknown — which the caller also counts.
                row = {"branch": b, "oid": expected, "result": "aborted", "detail": str(exc)}
                with contextlib.suppress(Incomplete):
                    probe = _run(["git", "-C", repo_root, "rev-parse", "--verify", "--quiet",
                                  f"refs/heads/{b}"])
                    if probe.returncode != 0 or not probe.stdout.strip():
                        row["result"] = "deleted"
                        row["detail"] = str(exc) + " (ref is gone — the delete landed)"
                results.append(row)
                raise
            if res.returncode == 0:
                results.append({"branch": b, "oid": expected, "result": "deleted", "detail": ""})
            else:
                results.append({"branch": b, "oid": expected, "result": "refused",
                                "detail": (res.stderr or res.stdout).strip()})
    except Incomplete:
        # Best-effort repair before surfacing the abort; the reconcile must never
        # mask the original error.
        with contextlib.suppress(Exception):
            _reconcile_held_deletions(repo_root, results)
        raise
    _reconcile_held_deletions(repo_root, results)
    return results


# ── main ────────────────────────────────────────────────────────────────────

def _disk_free_kb(path: str) -> int | None:
    import shutil as _shutil

    try:
        return _shutil.disk_usage(path).free // 1024
    except OSError:
        return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="branch_reaper",
        description="Reap provably-dead local branches, classified from GitHub PR state (#4408). "
                    "Dry-run by default.",
        # A destructive arming flag must be EXACT: without this, `--a`, `--ap`
        # and `--appl` all arm the delete path.
        allow_abbrev=False,
    )
    p.add_argument("--repo", default=None,
                   help="repo path or owner/name (default: cwd)")
    p.add_argument("--slug", default=None, help="GitHub owner/name override")
    p.add_argument("--main-ref", default=None, help="main ref for the ancestry test")
    p.add_argument("--apply", action="store_true", help="delete safe branches (default: dry-run)")
    p.add_argument("--include-closed-unmerged", action="store_true",
                   help="also delete PR-closed-unmerged branches (default: preserve)")
    p.add_argument("--reap-worktrees", action="store_true",
                   help="with --apply, first run scripts/pi-reap-worktrees.sh --apply")
    p.add_argument("--worktree-engine", default=None,
                   help="path to pi-reap-worktrees.sh (default: <repo>/scripts/pi-reap-worktrees.sh)")
    p.add_argument("--protect", action="append", default=[],
                   help="glob of branches to protect (repeatable)")
    p.add_argument("--report", default=None, help="write a markdown report to this path")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--backup-bundle", default=None,
                   help="write one git bundle of the deleted tips before deleting "
                        "(default on --apply: "
                        "<repo>/branch-reaper-backup-<epoch>-<random>.bundle)")
    p.add_argument("--no-backup", action="store_true",
                   help="do NOT write a backup bundle on --apply; the recovery record "
                        "then holds only SHAs, which do not survive object pruning")
    p.add_argument("--max-pr-pages", type=int, default=DEFAULT_MAX_PR_PAGES)
    p.add_argument("--engine-timeout", type=int, default=900)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # `_LANDED` is module-level state read by the handler below; reset it per call
    # so a second in-process `main()` (the test suite, or an embedder) cannot
    # inherit a previous run's deletions and report exit 6 for an unrelated
    # abort. The production CLI is one-shot, but the leak is silent when it bites.
    global _LANDED
    _LANDED = False
    # The true top-level handler: ANY Incomplete or OSError from anything below —
    # `resolve_repo`'s `os.getcwd()`, the main-checkout probe, enumeration, dirty
    # probes, `build_report`, the delete phase, the report writes, the post-delete
    # output block, or the post-teardown re-read — must surface as the documented
    # exit code, never an uncaught traceback. This is the BOUNDARY; the two richer
    # handlers inside `_run_main` add detail, but the after-deletion distinction
    # is applied HERE too, from `_LANDED`, so no path can return "nothing was
    # deleted" (2) after something was.
    try:
        return _run_main(args)
    except (Incomplete, OSError) as exc:
        if _LANDED:
            print(f"branch_reaper: INCOMPLETE AFTER DELETION — {exc}. At least one branch "
                  f"was deleted or left in an unknown state. The pre-delete recovery "
                  f"record holds every classified tip.", file=sys.stderr)
            return EXIT_INCOMPLETE_AFTER_DELETE
        print(f"branch_reaper: INCOMPLETE — {exc}.", file=sys.stderr)
        return EXIT_INCOMPLETE
    except (SystemExit, KeyboardInterrupt):
        # Deliberate exits are not faults — never relabel them.
        raise
    except BaseException as exc:
        # The boundary also owns the UNexpected — a malformed `gh` payload that
        # trips an AttributeError, or a bug in a helper. Without this the exit
        # table has no such code and Python's own exit 1 leaks out; after a
        # deletion that would drop the exit-6 signal entirely.
        if _LANDED:
            print(f"branch_reaper: INTERNAL ERROR AFTER DELETION — {exc!r}. At least "
                  f"one branch was deleted or left in an unknown state.",
                  file=sys.stderr)
            return EXIT_INCOMPLETE_AFTER_DELETE
        print(f"branch_reaper: INTERNAL ERROR — {exc!r}.", file=sys.stderr)
        return EXIT_INTERNAL


def _run_main(args) -> int:
    try:
        repo_root, slug = resolve_repo(args.repo)
    except ValueError as exc:
        print(f"branch_reaper: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.slug:
        if not _valid_slug(args.slug):
            print(f"branch_reaper: --slug must be exactly 'owner/name', got "
                  f"{args.slug!r}", file=sys.stderr)
            return EXIT_USAGE
        slug = args.slug

    # Validate the flag COMBINATION before anything is written. Raised inside the
    # `--apply` block it fired AFTER the recovery record and the report write, so
    # an aborted run still clobbered the previous run's recovery record — the only
    # durable record there is when that run passed `--no-backup`.
    if args.no_backup and args.backup_bundle:
        print("branch_reaper: --no-backup and --backup-bundle are mutually exclusive "
              "— --no-backup means 'write no bundle'.", file=sys.stderr)
        return EXIT_USAGE

    if args.apply and is_main_checkout(repo_root):
        print("branch_reaper: refusing --apply from the MAIN checkout — run from a linked "
              "worktree (this tool invokes `git update-ref -d` as an interpreter file "
              "payload, which main-worktree-guard does not content-gate).", file=sys.stderr)
        return EXIT_USAGE

    try:
        branches = enum_branches(repo_root)
        worktrees = enum_worktrees(repo_root)
        ref = args.main_ref or main_ref(repo_root)
        ancestors = enum_ancestors(repo_root, ref)
        prs = fetch_prs(slug, max_pages=args.max_pr_pages, per_page=DEFAULT_PER_PAGE)
    except Incomplete as exc:
        print(f"branch_reaper: INCOMPLETE — {exc}. Nothing was deleted.", file=sys.stderr)
        return EXIT_INCOMPLETE

    protected: set[str] = {default_branch(repo_root), "main", "master", "HEAD"}
    cur = current_branch(repo_root)
    if cur:
        protected.add(cur)
    main_wt_path = os.path.realpath(_main_wt(repo_root))
    for wt in worktrees:
        if wt.get("branch") and os.path.realpath(wt["path"]) == main_wt_path:
            protected.add(wt["branch"])
    for pat in args.protect:
        protected.update(b for b in branches if fnmatch.fnmatch(b, pat))

    rows = classify(branches, worktrees, prs, ancestors,
                    protected=protected,
                    include_closed_unmerged=args.include_closed_unmerged)
    # Annotate worktree-held rows with dirt (the issue requires dirty worktrees
    # reported, not silently treated as clean), and detached checkouts with age.
    for r in rows:
        if r["worktree"]:
            r["dirty"] = worktree_dirty(r["worktree"])
    detached_ts = _detached_head_ts(repo_root, worktrees)

    result = EXIT_OK
    engine_output: str | None = None
    apply_results: list[dict] | None = None
    disk: dict | None = None
    recovery: list[dict] | None = None

    if args.apply:
        try:
            if args.reap_worktrees:
                engine = args.worktree_engine or os.path.join(repo_root, "scripts", "pi-reap-worktrees.sh")
                rc, engine_output = run_worktree_engine(engine, repo_root, args.engine_timeout)
                if rc == _ENGINE_EXIT_FAILCLOSED:
                    print("branch_reaper: INCOMPLETE — the worktree engine fail-closed "
                          "(nothing trusted). Nothing was deleted.", file=sys.stderr)
                    return EXIT_INCOMPLETE
                if rc == _ENGINE_EXIT_USAGE:
                    print("branch_reaper: internal error — the worktree engine rejected its argv.",
                          file=sys.stderr)
                    return EXIT_INTERNAL
                if rc == _ENGINE_EXIT_PARTIAL:
                    result = EXIT_PARTIAL
                elif rc != _ENGINE_EXIT_OK:
                    print(f"branch_reaper: internal error — unexpected worktree-engine exit {rc}.",
                          file=sys.stderr)
                    return EXIT_INTERNAL

            # Refresh the worktree list after any delegated teardown and on EVERY
            # --apply path (not only --reap-worktrees), so the recovery record and
            # the report derive from a snapshot taken after the initial
            # enumeration (which preceded two paginated `gh api` calls and the
            # per-worktree dirty probes). This snapshot is for the REPORT and the
            # RECOVERY RECORD only: `delete_branches` re-reads its own, later
            # snapshot for the checked-out guard, because this one still precedes
            # the recovery write, the report write and `git bundle create` (up to
            # 900 s) — the window that was the P1.
            worktrees = enum_worktrees(repo_root)
            held_map = {wt["branch"]: wt["path"] for wt in worktrees if wt.get("branch")}
            for r in rows:
                r["worktree"] = held_map.get(r["branch"])
                # Refresh dirt and detached-HEAD age from the SAME snapshot the
                # report and the recovery record are built from: a stale
                # first-snapshot dirty=no would be printed for a genuinely dirty
                # late worktree. (`delete_branches` takes its OWN, later snapshot
                # for the actual checked-out guard — see its docstring.)
                if r["worktree"]:
                    r["dirty"] = worktree_dirty(r["worktree"])
            detached_ts = _detached_head_ts(repo_root, worktrees)
            # ALL SAFE rows, not just the un-held ones. A worktree holding one of
            # these can be released in the window between this write and
            # `delete_branches`' own held re-read (the report write, the bundle
            # write), and the branch IS then deleted — so excluding held rows can
            # leave a deleted tip absent from the only durable record. `git
            # update-ref -d` also removes that ref's reflog, so there is no
            # second chance.
            recovery = [{"branch": r["branch"], "oid": r["oid"], "verdict": r["reason"],
                         "pr_number": r["pr_number"]}
                        for r in rows if r["verdict"] == VERDICT_SAFE]
            # Durable recovery record BEFORE any deletion (so a crash mid-delete
            # still leaves the tips recoverable), independent of --report.
            recovery_path = (args.report + ".recovery.json") if args.report else \
                os.path.join(repo_root, "branch-reaper-recovery.json")
            _write_json_safe(recovery_path, {"generated": _now(), "repo": repo_root,
                                             "include_closed_unmerged": args.include_closed_unmerged,
                                             "branches": recovery}, repo_root)
            if args.report:
                _write_text_safe(args.report, build_report(
                    rows, worktrees, ancestors, repo_root=repo_root, slug=slug,
                    main_ref_used=ref, include_closed_unmerged=args.include_closed_unmerged,
                    engine_output=engine_output, recovery=recovery, detached_ts=detached_ts),
                    repo_root)
            free_before = _disk_free_kb(repo_root)
            apply_results = []
            # delete_branches re-reads the checked-out set itself, after the
            # bundle write and again every HELD_RECHECK_BATCH deletes: the caller
            # snapshot above is for the report and the recovery record only.
            # Default the bundle ON for --apply: the SHAs in the recovery record
            # are not durable on their own (`update-ref -d` removes the deleted
            # ref's reflog), so an unbacked-up apply becomes unrecoverable once gc
            # prunes. `--no-backup` is the explicit opt-out.
            backup_bundle = args.backup_bundle
            if args.no_backup:
                print("branch_reaper: --no-backup — the recovery record holds only SHAs, "
                      "which do NOT survive object pruning (`gc.pruneExpire`, 2 weeks by "
                      "default). Once pruned, the deleted tips are unrecoverable.",
                      file=sys.stderr)
            if backup_bundle is None and not args.no_backup:
                backup_bundle = os.path.join(
                    repo_root, f"branch-reaper-backup-{_now()}-{os.urandom(3).hex()}.bundle")
            delete_branches(repo_root, rows, backup_bundle=backup_bundle,
                            results=apply_results)
            _mark_landed(apply_results)
            free_after = _disk_free_kb(repo_root)
            if free_before is not None and free_after is not None:
                disk = {"before_kb": free_before, "after_kb": free_after,
                        "delta_kb": free_after - free_before}
            if any(r["result"] == "refused" for r in apply_results):
                result = EXIT_PARTIAL
        except (Incomplete, OSError) as exc:
            # ORDER MATTERS: Python matches handlers in source order, so this
            # clause MUST precede the `except BaseException` below. Placing
            # `BaseException` first made this handler dead and put the
            # refused->EXIT_PARTIAL tail inside unreachable code — silently
            # turning the documented exit 4 into a 0. Caught by review.
            _mark_landed(apply_results)
            landed = _landed_results(apply_results)
            if landed:
                print(f"branch_reaper: INCOMPLETE AFTER DELETION — {exc}. "
                      f"{len(landed)} branch(es) were deleted, or left in an unknown state "
                      f"(a killed `git update-ref -d` may already have landed). The pre-delete "
                      f"recovery record holds every classified tip.", file=sys.stderr)
                return EXIT_INCOMPLETE_AFTER_DELETE
            print(f"branch_reaper: INCOMPLETE — {exc}. Delete phase aborted; the "
                  f"pre-delete recovery record is on disk.", file=sys.stderr)
            return EXIT_INCOMPLETE
        except BaseException:
            # Everything else — recorded then propagated. A raise from inside the
            # delete phase still leaves `apply_results` holding what landed, and
            # the top-level handler in `main` must not report "nothing was
            # deleted" over a real deletion.
            _mark_landed(apply_results)
            raise

    try:
        # INSIDE the try, not before it. `build_report` sits after the delete
        # phase, so an OSError from it (its timestamp formatting raises for an
        # out-of-range clock) is an AFTER-DELETION failure and must take the 6
        # branch below. Between the two handlers it reached only the top-level
        # catch and returned exit 2 — whose documented meaning, "Nothing was
        # deleted", is false by then.
        report = build_report(rows, worktrees, ancestors, repo_root=repo_root, slug=slug,
                              main_ref_used=ref,
                              include_closed_unmerged=args.include_closed_unmerged,
                              engine_output=engine_output, apply_results=apply_results, disk=disk,
                              recovery=recovery, detached_ts=detached_ts)
        if args.report:
            _write_text_safe(args.report, report, repo_root)
    except (Incomplete, OSError) as exc:
        # Same token, same after-deletion distinction, same boundary catch as the
        # handler above — this is the POST-delete write, so an OSError here is
        # precisely the case whose exit-6 signal used to be lost to a traceback.
        landed = _landed_results(apply_results)
        if landed:
            print(f"branch_reaper: INCOMPLETE AFTER DELETION — {exc}. "
                  f"{len(landed)} branch(es) were deleted. The pre-delete recovery "
                  f"record holds every classified tip.", file=sys.stderr)
            return EXIT_INCOMPLETE_AFTER_DELETE
        print(f"branch_reaper: INCOMPLETE — {exc}", file=sys.stderr)
        return EXIT_INCOMPLETE
    if args.json:
        print(json.dumps({"repo": repo_root, "slug": slug, "main_ref": ref,
                          "rows": rows,
                          "worktrees": worktrees,
                          "apply_results": apply_results,
                          "exit": result}, indent=2))
    else:
        safe = [r for r in rows if r["verdict"] == VERDICT_SAFE]
        deletable = [r for r in safe if not r["worktree"]]
        print(f"branch_reaper: {'APPLY' if args.apply else 'DRY-RUN'} repo={repo_root} "
              f"branches={len(rows)} safe={len(safe)} deletable_now={len(deletable)} "
              f"preserve={sum(1 for r in rows if r['verdict'] == VERDICT_PRESERVE)} "
              f"judgement={sum(1 for r in rows if r['verdict'] == VERDICT_JUDGEMENT)}")
        if apply_results is not None:
            print(f"  deleted={sum(1 for r in apply_results if r['result'] == 'deleted')} "
                  f"restored={sum(1 for r in apply_results if r['result'] == 'restored')} "
                  f"refused={sum(1 for r in apply_results if r['result'] == 'refused')} "
                  f"skipped={sum(1 for r in apply_results if r['result'] == 'skipped')}")
        if args.report:
            print(f"  report: {args.report}")
        elif args.apply:
            print(f"  recovery: {os.path.join(repo_root, 'branch-reaper-recovery.json')}")
    return result


def _main_wt(repo_root: str) -> str:
    listing = _run(["git", "-C", repo_root, "worktree", "list", "--porcelain"])
    for line in listing.stdout.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):]
    return repo_root


if __name__ == "__main__":
    sys.exit(main())
