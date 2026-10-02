#!/usr/bin/env python3
"""who_is_on — the human-facing "who is on #N?" verb, and the stranded-work
inventory (#4256).

Why this exists
---------------
The natural one-call way to ask "is anyone working on #N?" is a single `gh`
query that enumerates REMOTE refs / open PRs / assignees. That check cannot see
work that exists only on THIS checkout — a local-only branch that was never
pushed, or a worktree holding uncommitted changes. On a hub with ~1470 local
refs and ~410 worktrees, the one-call check reported live work as FREE: #2924's
P1 fix (~80 uncommitted lines in `.worktrees/fix-2924-3144-health-idle`) sat
invisible for six days and nearly got a second lane dispatched onto the same
code path (#4256).

`tools/collision_preflight.py` already answers correctly — it enumerates local
branches and worktrees too — but it is a DISPATCH GATE: it exits 1 on a hit
(including a keyword-only hit) and exits 2 when a surface cannot be queried.
That makes it the wrong thing to reach for when the question is "who holds
#N?", not "may I dispatch?".

This tool is that question as a supported verb. It REUSES the pre-flight's own
machinery rather than copying it — `run_preflight` (surface enumeration +
number/keyword matching), `_resolve_target` / `_ambiguity_refusal` (repo
resolution) and `_git_refs` / `_worktree_blocks` (git enumeration) — so the two
cannot disagree about what a holder is. It *answers* instead of gating:

  rc 0  a COMPLETE answer, with or without holders. A hit is information, not
        a collision; this tool never returns 1.
  rc 2  an INCOMPLETE answer: a surface could not be queried, the target repo
        could not be resolved unambiguously, a local holder's uncommitted state
        could not be read, or (inventory) a worktree's uncommitted state could
        not be read. "Could not check" is never reported as "nobody".
  rc 3  usage error (bad flag/value, or a `--repo` that is neither a directory
        nor an `owner/name`).

Modes
-----
  who-is-on.sh <N> [--repo <path|owner/name>]
      Who holds issue #N. Holders are grouped LOCAL (local-only branches and
      worktrees — the surfaces the ad-hoc check misses) then REMOTE/GITHUB.
      Each local holder is annotated with ahead/behind, last-commit age and
      whether a worktree holds uncommitted changes.

  who-is-on.sh --inventory [--limit N] [--dirty-only] [--detached] [--repo ...]
      The stranded-work inventory: local-only branches (no remote-tracking
      counterpart under ANY remote), each with its ahead count, last-commit age
      and whether a worktree holds uncommitted changes. A report, not a reaper
      — it lists nothing it is willing to delete (#4256 ask 1). `--dirty-only`
      narrows to the safety-critical subset: branches whose only copy may be on
      disk (an UNKNOWN state is INCLUDED, never dropped). `--detached`
      additionally counts detached worktrees holding uncommitted work (off by
      default: it costs one `git status` per worktree).

Performance note
----------------
`git status` costs ~0.7 s per worktree on this hub, so both modes stat only the
worktrees they must (the holders' worktrees for a question; the local-only
branches' worktrees for the inventory) rather than all ~410. Branch ahead/age
comes from one `git for-each-ref` (`%(ahead-behind:…)`), not a call per branch.

This tool never weakens `collision_preflight.py`; the gate's behaviour is
unchanged and it is not replaced. See #4256.
"""
from __future__ import annotations

import argparse
import math
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

# The pre-flight owns the surface enumeration and target resolution; importing
# it keeps ONE implementation of matching and git enumeration (a second one is
# how the two checks drift apart — the bug class this tool exists to expose).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import collision_preflight as cp

EXIT_ANSWERED = 0
EXIT_INCOMPLETE = 2
EXIT_USAGE = 3

DEFAULT_INVENTORY_LIMIT = 40
DEFAULT_TIMEOUT = 60.0
# How far back a local-only branch is measured from. Every lane here branches
# off main, so origin/main is the honest base; a local main is the fallback for
# a checkout with no remote.
BASE_CANDIDATES = ("origin/main", "main")


class _UsageError(Exception):
    """argparse rejected the invocation. Must map to EXIT_USAGE (3), never to
    EXIT_INCOMPLETE (2) — a misuse is not a "could not check"."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # pragma: no cover - exercised via main()
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise _UsageError(message)


def _git(git_bin: str, repo: str, *args: str, timeout: float = DEFAULT_TIMEOUT):
    """Run git; never raise for a non-zero exit. Returns (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(
            [git_bin, *args], cwd=repo, capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"{git_bin}: command not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"git {' '.join(args)}: timeout after {timeout:g}s"
    except (OSError, ValueError, OverflowError) as exc:  # non-finite timeout etc.
        return 126, "", f"{git_bin}: {exc}"


def _clean(text: str | None) -> str:
    """Untrusted text safe to PRINT. The gate keeps two levels: `_sanitize`
    drops control characters (its display path) and `_strip_control_sequences`
    also removes a whole escape sequence with its payload (its matching path).
    For output we want the stronger one — an OSC 52 clipboard overwrite or a
    `CSI 2J` that blanks the ANSWER must not leave its payload as literal text.
    """
    return cp._strip_control_sequences(text)


def human_age(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 36:
        return f"{hours}h"
    days = hours // 24
    if days < 45:
        return f"{days}d"
    months = days // 30
    if months < 18:
        return f"{months}mo"
    return f"{days // 365}y"


def base_ref(git_bin: str, repo: str, timeout: float = DEFAULT_TIMEOUT) -> str | None:
    """The first BASE_CANDIDATE that exists (origin/main, else main, else None)."""
    for candidate in BASE_CANDIDATES:
        if _git(git_bin, repo, "rev-parse", "--verify", "--quiet", candidate,
                timeout=timeout)[0] == 0:
            return candidate
    return None


def branch_name(ref: str) -> str:
    """`refs/heads/fix/x` -> `fix/x`; anything else unchanged."""
    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref


def local_branch_refs(git_bin: str, repo: str,
                      timeout: float = DEFAULT_TIMEOUT) -> list[str]:
    """FULL local refs (`refs/heads/…`), enumerated by the gate's own helper so
    the two tools share one definition of "a local branch"."""
    return cp._git_refs(git_bin, repo, "refs/heads", timeout)


def pushed_branch_names(git_bin: str, repo: str,
                        timeout: float = DEFAULT_TIMEOUT) -> set[str]:
    """Branch short-names reachable from any remote-tracking ref. A branch
    pushed to ANY remote is not local-only. Remote names come from `git remote`
    so a remote whose name contains a slash cannot mis-split the ref."""
    refs = cp._git_refs(git_bin, repo, "refs/remotes", timeout)
    rc, out, _ = _git(git_bin, repo, "remote", timeout=timeout)
    # LONGEST remote prefix first: with remotes `foo` and `foo/bar`,
    # `refs/remotes/foo/bar/qux` must strip `foo/bar` (-> `qux`), not `foo`
    # (-> `bar/qux`). A shortest-first match hid a never-pushed branch named
    # `bar/qux` from every surface — the exact stranded-work fail-open.
    remotes = sorted((ln.strip() for ln in out.splitlines() if ln.strip()),
                     key=len, reverse=True) if rc == 0 else []
    names: set[str] = set()
    for ref in refs:
        rest = ref[len("refs/remotes/"):] if ref.startswith("refs/remotes/") else ref
        for remote in remotes:
            if rest.startswith(remote + "/"):
                names.add(rest[len(remote) + 1:])
                break
        else:
            # No known remote prefix (e.g. a remote removed but its tracking ref
            # left behind): fall back to stripping one path component.
            names.add(rest.split("/", 1)[1] if "/" in rest else rest)
    return names


def local_only_refs(git_bin: str, repo: str,
                    timeout: float = DEFAULT_TIMEOUT) -> list[str]:
    """FULL local refs with no remote-tracking counterpart under any remote —
    the refs the ad-hoc remote-only check cannot see."""
    pushed = pushed_branch_names(git_bin, repo, timeout)
    return [r for r in local_branch_refs(git_bin, repo, timeout) if branch_name(r) not in pushed]


def branches_meta(git_bin: str, repo: str,
                  timeout: float = DEFAULT_TIMEOUT) -> tuple[dict[str, dict], str | None]:
    """One `for-each-ref` for every local branch, keyed by FULL ref: ahead count
    vs the base, last-commit age, subject. Returns ({ref: meta}, base_used).

    `%(refname)` (not `%(refname:short)`) is deliberate: short names are
    disambiguated by namespace, so a tag named like a branch makes the branch
    report as `heads/<name>` — and every lookup keyed on the branch name (which
    is what `local_only_refs` and the worktree map produce) then misses, silently
    degrading ahead/age/subject to `?` (#4256 review). The key must match the
    FULL refs used everywhere else in this module."""
    base = base_ref(git_bin, repo, timeout)
    fields = ["%(refname)"]
    if base:
        fields.append(f"%(ahead-behind:{base})")
    fields += ["%(committerdate:unix)", "%(subject)"]
    # A REAL tab, not ``%x09`` — for-each-ref on Apple Git emits ``%x09``
    # literally (it is a `git log --format` escape, not a for-each-ref one).
    fmt = "\t".join(fields)
    rc, out, err = _git(git_bin, repo, "for-each-ref", f"--format={fmt}", "refs/heads",
                        timeout=timeout)
    if rc != 0:
        raise cp.SurfaceError(f"git for-each-ref refs/heads failed (exit {rc}): {err.strip()}")
    now = time.time()
    meta: dict[str, dict] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < (4 if base else 3):
            continue
        ref = parts[0]
        if base:
            ab, ctime, subject = parts[1], parts[2], "\t".join(parts[3:])
            ahead = None
            if " " in ab:
                try:
                    ahead = int(ab.split()[0])
                except ValueError:
                    ahead = None
        else:
            ahead, ctime, subject = None, parts[1], "\t".join(parts[2:])
        try:
            age = now - float(ctime)
        except ValueError:
            age = None
        meta[ref] = {"ahead": ahead, "age_seconds": age, "subject": subject}
    return meta, base


def worktree_map(git_bin: str, repo: str, timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    """Every worktree with its branch ref — ONE `git worktree list` call, no
    status. Reuses the gate's porcelain parser so the two agree on worktrees."""
    rc, out, err = _git(git_bin, repo, "worktree", "list", "--porcelain", timeout=timeout)
    if rc != 0:
        raise cp.SurfaceError(f"git worktree list failed (exit {rc}): {err.strip()}")
    entries: list[dict] = []
    for block in cp._worktree_blocks(out):
        raw = block.get("branch", "")
        # `_worktree_blocks` emits the literal "(detached)" — truthy, so it must
        # be normalised to "" or every detached worktree is silently skipped
        # (which made `--detached` a no-op that reported "0 uncommitted").
        branch_ref = "" if raw in ("", "(detached)", "detached") else raw
        entries.append({"path": block.get("path", ""), "branch_ref": branch_ref})
    return entries


# Uncommitted-state vocabulary. "could not read" is NEVER "clean".
DS_CLEAN = "clean"
DS_DIRTY = "dirty"
DS_MISSING = "missing"    # the worktree directory is gone (prunable)
DS_UNKNOWN = "unknown"    # git status failed on an existing directory


def worktree_state(git_bin: str, path: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[str, int]:
    """(state, uncommitted_file_count). `state` is DS_CLEAN / DS_DIRTY /
    DS_MISSING / DS_UNKNOWN. A non-zero `git status` is UNKNOWN, never CLEAN —
    conflating them is the fail-open this tool exists to close. Absence is only
    DS_MISSING when the path is CONFIRMED gone (`lstat` ENOENT); an unreadable
    or non-directory path is DS_UNKNOWN."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return DS_MISSING, 0
    except OSError:
        return DS_UNKNOWN, 0
    if not stat.S_ISDIR(st.st_mode):
        return DS_UNKNOWN, 0
    rc, out, _ = _git(git_bin, path, "status", "--porcelain", timeout=timeout)
    if rc != 0:
        return DS_UNKNOWN, 0
    count = len([ln for ln in out.splitlines() if ln.strip()])
    return (DS_DIRTY if count else DS_CLEAN), count


def _is_at_risk(state: str) -> bool:
    """Rows that belong in the safety-critical subset: a dirty worktree, one
    whose state could not be read, or one whose directory is CONFIRMED gone (its
    only copy is no longer on disk). NEVER drop an unknown or a missing from
    `--dirty-only`."""
    return state in (DS_DIRTY, DS_UNKNOWN, DS_MISSING)


def inventory(repo: str, git_bin: str = "git", timeout: float = DEFAULT_TIMEOUT,
              limit: int = DEFAULT_INVENTORY_LIMIT, dirty_only: bool = False,
              include_detached: bool = False) -> tuple[list[dict], dict]:
    """Local-only branches annotated with ahead/age/uncommitted state.

    Returns (rows_shown, summary). Rows are sorted at-risk-first, then
    oldest-first — the stranded copy with uncommitted edits is the one that
    matters, and the oldest is the one most likely to be stale and forgotten.
    `git status` runs only for the worktrees a local-only branch actually has.
    """
    meta, base = branches_meta(git_bin, repo, timeout)
    only = local_only_refs(git_bin, repo, timeout)
    worktrees = worktree_map(git_bin, repo, timeout)
    wt_by_ref = {w["branch_ref"]: w for w in worktrees if w["branch_ref"]}

    rows: list[dict] = []
    for ref in sorted(only):
        wt = wt_by_ref.get(ref)
        m = meta.get(ref, {})
        if wt:
            state, dirty = worktree_state(git_bin, wt["path"], timeout)
            worktree_path = wt["path"]
        else:
            state, dirty, worktree_path = "none", 0, ""
        rows.append({
            "branch": branch_name(ref),
            "ahead": m.get("ahead"),
            "base": base,
            "age_seconds": m.get("age_seconds"),
            "subject": m.get("subject", ""),
            "worktree": worktree_path,
            "state": state,
            "dirty": dirty,
        })

    detached_dirty = detached_unknown = 0
    if include_detached:
        for entry in worktrees:
            if entry["branch_ref"]:
                continue
            state, _count = worktree_state(git_bin, entry["path"], timeout)
            if state == DS_DIRTY:
                detached_dirty += 1
            elif state == DS_UNKNOWN:
                detached_unknown += 1

    uncommitted = [r for r in rows if r["state"] == DS_DIRTY]
    unknown = [r for r in rows if r["state"] == DS_UNKNOWN]
    missing = [r for r in rows if r["state"] == DS_MISSING]
    at_risk = [r for r in rows if _is_at_risk(r["state"])]

    summary = {
        "local_only": len(rows),
        "uncommitted": len(uncommitted),
        "unknown": len(unknown),
        "missing": len(missing),
        "at_risk": len(at_risk),
        "detached_uncommitted": detached_dirty,
        "detached_unknown": detached_unknown,
        "shown": 0,
        "base": base,
        "dirty_only": dirty_only,
    }
    selected = at_risk if dirty_only else rows
    # at-risk first (dirty before unknown), then oldest first.
    order = {DS_DIRTY: 0, DS_UNKNOWN: 1}
    selected.sort(key=lambda r: (order.get(r["state"], 2), -(r["age_seconds"] or 0)))
    summary["shown"] = min(len(selected), max(0, limit))
    return selected[: max(0, limit)], summary


def _local_detail_lines(surfaces: list, git_bin: str, repo: str,
                        timeout: float) -> tuple[list[str], list[str]]:
    """Ahead/behind + age + uncommitted state for every LOCAL branch holder.

    The part the remote-only check cannot produce. Only the holders' own
    worktrees are stat'd. An unreadable worktree prints `?`, never `clean`.
    Returns (lines, refs_whose_uncommitted_state_could_not_be_read) — the second
    list keeps `who_holds` fail-closed: a holder whose state we could not read is
    not a complete answer.
    """
    lines: list[str] = []
    unknown_refs: list[str] = []
    hit_refs: list[str] = []
    for surface in surfaces:
        if surface.name != cp.SURFACE_LOCAL_BRANCHES:
            continue
        hit_refs.extend(hit.ref for hit in surface.hits)
    if not hit_refs:
        return lines, unknown_refs

    meta, base = branches_meta(git_bin, repo, timeout)
    map_failed = False
    try:
        wt_by_ref = {w["branch_ref"]: w for w in worktree_map(git_bin, repo, timeout)
                     if w["branch_ref"]}
    except cp.SurfaceError:
        # A failed map means we cannot say ANY holder is clean — mark them all
        # unreadable rather than printing a bare "no worktree".
        wt_by_ref = {}
        map_failed = True

    for ref in hit_refs:
        m = meta.get(ref, {})
        ahead = m.get("ahead")
        ahead_txt = f"ahead {ahead} of {base}" if ahead is not None and base else "ahead ?"
        age_txt = human_age(m.get("age_seconds"))
        wt = wt_by_ref.get(ref)
        if map_failed:
            dirty_txt = "could not read the worktree list — NOT clean"
            unknown_refs.append(branch_name(ref))
        elif wt is None:
            dirty_txt = "no worktree"
        else:
            state, count = worktree_state(git_bin, wt["path"], timeout)
            if state == DS_DIRTY:
                dirty_txt = f"{count} uncommitted file(s) in {_clean(wt['path'])}"
            elif state == DS_CLEAN:
                dirty_txt = "clean"
            elif state == DS_MISSING:
                dirty_txt = f"worktree missing ({_clean(wt['path'])})"
            else:
                dirty_txt = (f"could not read uncommitted state "
                             f"({_clean(wt['path'])}) — NOT clean")
                unknown_refs.append(branch_name(ref))
        # The unreadable refs are collected for EVERY holder (fail-closed), but
        # only the first MAX_HITS_SHOWN lines are printed — the same cap the hit
        # lists use, so a broad match cannot flood the report.
        if len(lines) < cp.MAX_HITS_SHOWN:
            lines.append(f"      {_clean(branch_name(ref))}: {ahead_txt} · last commit "
                         f"{age_txt} ago · {dirty_txt}")
    if len(hit_refs) > cp.MAX_HITS_SHOWN:
        lines.append(f"      … +{len(hit_refs) - cp.MAX_HITS_SHOWN} more local holder(s) "
                     f"(total {len(hit_refs)})")
    return lines, unknown_refs


def _hit_lines(surface) -> list[str]:
    """Per-surface hit lines, capped exactly like the gate's report so a broad
    keyword match on a 550-branch hub cannot print hundreds of lines. Every field
    is sanitized: refs/paths are untrusted and the report is what a human reads."""
    shown = surface.hits[: cp.MAX_HITS_SHOWN]
    tag = {"strong": "number", "keyword": "keyword", "weak": "weak"}
    out = [f"  [{_clean(surface.name)}] {_clean(hit.ref)} — "
           f"{_clean(hit.detail)} ({tag.get(hit.strength, hit.strength)})" for hit in shown]
    if len(surface.hits) > len(shown):
        out.append(f"  [{_clean(surface.name)}] … +{len(surface.hits) - len(shown)} "
                   f"more hit(s) on this surface (total {len(surface.hits)})")
    return out


def who_holds(issue: int, repo_arg: str | None, gh_bin: str, git_bin: str, timeout: float,
              keywords: str | None = None,
              min_keywords: int = cp.DEFAULT_MIN_KEYWORDS) -> tuple[str, int]:
    """The answer to "who is on #N?", local surfaces included. rc 0 = complete
    answer (holders or none); rc 2 = a surface could not be queried / the target
    repo could not be resolved. Never 1."""
    ns = SimpleNamespace(repo=repo_arg, gh=gh_bin, git=git_bin, issue=issue)
    target, code = cp._resolve_target(ns, timeout)
    if target is None:
        return (f"who-is-on: could not resolve the target repo for #{issue} "
                f"(see stderr).\n", code)
    refusal = cp._ambiguity_refusal(ns, target, timeout)
    if refusal is not None:
        return (f"ANSWER INCOMPLETE: the target repo for #{issue} could not be "
                f"resolved unambiguously (see stderr). This is NOT \"nobody\".\n", refusal)

    local_repo = target.path or os.getcwd()
    try:
        surfaces, title, _kws, _issue, _ = cp.run_preflight(
            issue, target, gh_bin, git_bin, timeout, explicit_keywords=keywords,
            min_keywords=min_keywords, identity=cp.Identity(login=None),
        )
    except cp.SurfaceError as exc:
        return f"who-is-on: {_clean(str(exc))}\n", EXIT_INCOMPLETE
    except Exception as exc:  # FAIL-CLOSED by contract
        # The one hard promise this tool makes is that it never returns 1. A
        # non-UTF-8 refname or worktree path makes the gate's `subprocess.run
        # (text=True)` raise UnicodeDecodeError (a ValueError, which `_run` does
        # not catch), and that would otherwise escape `main` as a traceback +
        # exit 1 — read by a caller as the collision code. Any unexpected failure
        # is an INCOMPLETE answer, never a crash and never "nobody".
        return (f"ANSWER INCOMPLETE: the pre-flight failed unexpectedly "
                f"({type(exc).__name__}: {_clean(str(exc))}). This is NOT \"nobody\".\n",
                EXIT_INCOMPLETE)

    local_surfaces = (cp.SURFACE_LOCAL_BRANCHES, cp.SURFACE_WORKTREES)
    incomplete = [s for s in surfaces if s.status == cp.STATUS_INCOMPLETE or s.truncated]
    blind = [s for s in surfaces if s.blind and s.status == cp.STATUS_CLEAN]
    # A holder is a NUMBER match (strong) or a keyword match; a weak prose
    # cross-reference is explicitly not work — the gate says so too, and
    # counting it would answer "someone is on it" for a passing mention.
    strong = [(s, h) for s in surfaces for h in s.hits if h.strength == "strong"]
    keyword = [(s, h) for s in surfaces for h in s.hits if h.strength == "keyword"]
    weak = [(s, h) for s in surfaces for h in s.hits if h.strength == "weak"]
    holders = strong + keyword

    lines: list[str] = []
    lines.append(f"who is on #{issue}?  —  repo: {_clean(target.slug or '(unresolved)')}"
                 + (f"  [{_clean(local_repo)}]" if target.path else ""))
    cleaned_title = " ".join(_clean(title).split())
    lines.append(f"title: {cleaned_title or '(unavailable)'}")
    lines.append("")
    lines.append("LOCAL — work that exists only on this checkout (the surfaces a")
    lines.append("        remote-only `gh` check cannot see):")
    for name in local_surfaces:
        surface = next((s for s in surfaces if s.name == name), None)
        if surface is None:
            continue
        if not surface.hits:
            lines.append(f"  [{surface.name}] none")
            continue
        lines.extend(_hit_lines(surface))
    detail_lines, unknown_detail = _local_detail_lines(surfaces, git_bin, local_repo, timeout)
    lines.extend(detail_lines)
    lines.append("")
    lines.append("REMOTE / GITHUB:")
    remote_any = False
    for surface in surfaces:
        if surface.name in local_surfaces or not surface.hits:
            continue
        remote_any = True
        lines.extend(_hit_lines(surface))
    if not remote_any:
        lines.append("  (no remote branch, open/closed PR, assignee or claim comment)")
    if weak and not holders:
        lines.append("")
        lines.append(f"WEAK SIGNALS (non-blocking — prose is not work): {len(weak)} "
                     "prose-only cross-reference(s); these do NOT make #N held.")
    if blind:
        lines.append("")
        lines.append(f"BLIND: {', '.join(s.name for s in blind)} yielded no distinctive "
                     "signal (e.g. an all-generic issue title) — not a clean surface, "
                     "just an empty one.")
    if incomplete:
        lines.append("")
        lines.append("INCOMPLETE SURFACES — could not be queried:")
        for surface in incomplete:
            lines.append(f"  [{_clean(surface.name)}] "
                         f"{_clean(surface.truncation_note or surface.note)}")
    if unknown_detail:
        lines.append("")
        lines.append("UNREADABLE LOCAL STATE — could not check uncommitted work:")
        lines.append("  " + ", ".join(unknown_detail) + " — NOT clean")
    lines.append("")
    if incomplete or unknown_detail:
        blame = []
        if incomplete:
            blame.append(f"{len(incomplete)} surface(s) could not be queried "
                         f"({', '.join(s.name for s in incomplete)})")
        if unknown_detail:
            blame.append(f"{len(unknown_detail)} local holder(s) had unreadable worktree "
                         f"state ({', '.join(unknown_detail)})")
        lines.append(
            f"ANSWER INCOMPLETE: {len(holders)} local/remote signal(s) found, but "
            + "; ".join(blame)
            + ". This is NOT \"nobody\" — fix the cause and re-run."
        )
        return "\n".join(lines) + "\n", EXIT_INCOMPLETE
    if holders:
        n_surfaces = len({s.name for s, _ in holders})
        ans = f"ANSWER: {len(holders)} holder(s) across {n_surfaces} surface(s)"
        if keyword:
            ans += f" ({len(strong)} by issue-number, {len(keyword)} keyword-only)"
        lines.append(ans + " — local-only branches and worktrees WERE enumerated.")
    else:
        caveat = (f" {len(blind)} surface(s) were BLIND (no distinctive signal), "
                  "so the answer rests on number matching and the local surfaces." if blind
                  else f" all {len(cp.ALL_SURFACES)} surfaces queried.")
        lines.append(
            f"ANSWER: nobody —{caveat} Local-only branches and worktrees WERE enumerated."
        )
    return "\n".join(lines) + "\n", EXIT_ANSWERED


def format_inventory(repo: str, rows: list[dict], summary: dict) -> str:
    lines: list[str] = []
    lines.append(f"stranded-work inventory  —  repo: {_clean(repo)}")
    lines.append("A report, not a reaper: nothing here is deleted or pruned. "
                 "Local-only = no remote-tracking counterpart (#4256).")
    lines.append("")
    base = summary.get("base") or "?"
    danger = (f"{summary['local_only']} local-only branch(es); {summary['uncommitted']} "
              "carry uncommitted work")
    if summary["unknown"]:
        danger += (f"; {summary['unknown']} could NOT be read (git status failed) — "
                   "NOT known-clean")
    if summary["missing"]:
        danger += f"; {summary['missing']} worktree dir(s) missing"
    if summary["detached_uncommitted"] or summary["detached_unknown"]:
        danger += (f"; detached worktrees: {summary['detached_uncommitted']} dirty, "
                   f"{summary['detached_unknown']} unreadable")
    lines.append(danger + f"  (ahead measured from {base})")
    lines.append("")
    if not rows:
        lines.append("  (none)")
        return "\n".join(lines) + "\n"
    lines.append(f"{'STATE':<8} {'BRANCH':<52} {'AHEAD':<6} {'AGE':<5} WORKTREE")
    for row in rows:
        state = {DS_DIRTY: "DIRTY", DS_UNKNOWN: "UNKNOWN", DS_MISSING: "MISSING",
                 DS_CLEAN: "clean"}.get(row["state"], "—")
        ahead = "?" if row["ahead"] is None else str(row["ahead"])
        age = human_age(row["age_seconds"])
        wt = row["worktree"]
        wt_txt = (f"{wt}  ({row['dirty']} file(s))"
                  if wt and row["state"] == DS_DIRTY else (wt or "—"))
        lines.append(f"{state:<8} {_clean(row['branch']):<52} {ahead:<6} {age:<5} "
                     f"{_clean(wt_txt)}")
    total = summary.get("at_risk", summary["uncommitted"] + summary["unknown"]) \
        if summary.get("dirty_only") else summary["local_only"]
    if summary["shown"] < total:
        lines.append("")
        lines.append(f"  … showing {summary['shown']} of {total}; raise --limit to see more.")
    return "\n".join(lines) + "\n"


def _resolve_inventory_path(repo_arg: str | None, gh_bin: str, git_bin: str,
                            timeout: float) -> tuple[str | None, int]:
    """A local checkout path for the inventory. `--repo` accepts the same two
    forms the gate accepts (owner/name OR a path)."""
    ns = SimpleNamespace(repo=repo_arg, gh=gh_bin, git=git_bin, issue=0)
    target, code = cp._resolve_target(ns, timeout)
    if target is None:
        return None, code
    if not target.path:
        print(f"who-is-on: no local checkout found for {target.slug or repo_arg!r}; the "
              "inventory reads local git state and needs one", file=sys.stderr)
        return None, EXIT_INCOMPLETE
    return target.path, 0


def build_parser() -> _Parser:
    parser = _Parser(
        prog="who_is_on",
        description="Who is on #N? — local-only branches and worktrees included (#4256).",
    )
    parser.add_argument("issue", nargs="?", type=int,
                        help="issue number to ask about, e.g. 4256 (omit for --inventory)")
    parser.add_argument("--inventory", action="store_true",
                        help="list local-only branches with ahead/age/uncommitted state "
                             "instead of asking about an issue")
    parser.add_argument("--dirty-only", action="store_true",
                        help="--inventory: only local-only branches at risk — a dirty "
                             "worktree, or one whose state could not be read")
    parser.add_argument("--detached", action="store_true",
                        help="--inventory: also scan detached worktrees for uncommitted work "
                             "(costs one `git status` per worktree)")
    parser.add_argument("--limit", type=int, default=DEFAULT_INVENTORY_LIMIT,
                        help=f"--inventory rows to show (default {DEFAULT_INVENTORY_LIMIT})")
    parser.add_argument("--repo", default=None,
                        help="target repo: `owner/name` OR a path to a checkout (default: cwd)")
    parser.add_argument("--keywords", default=None,
                        help="comma-separated keyword override when gh cannot supply the title")
    parser.add_argument("--min-keywords", type=int, default=cp.DEFAULT_MIN_KEYWORDS,
                        help=f"distinct keywords required for a keyword hit "
                             f"(default {cp.DEFAULT_MIN_KEYWORDS})")
    parser.add_argument("--gh", default=os.environ.get("COLLISION_PREFLIGHT_GH", "gh"),
                        help="gh binary (env COLLISION_PREFLIGHT_GH)")
    parser.add_argument("--git", default=os.environ.get("COLLISION_PREFLIGHT_GIT", "git"),
                        help="git binary (env COLLISION_PREFLIGHT_GIT)")
    # Deliberately NO ``type=float``: a bad value must be EXIT_USAGE, and an
    # eagerly-converted env default raises an uncaught ValueError (-> exit 1,
    # the one code this tool must never return). Mirrors collision_preflight.
    parser.add_argument("--timeout",
                        default=os.environ.get("COLLISION_PREFLIGHT_TIMEOUT", str(DEFAULT_TIMEOUT)),
                        metavar="SECS",
                        help="per-command timeout in seconds "
                             "(env COLLISION_PREFLIGHT_TIMEOUT)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        # Cross-argument checks raise through parser.error -> _UsageError, so they
        # are inside the guard: an uncaught _UsageError traceback exits 1, the one
        # code this tool must never return.
        if args.issue is None and not args.inventory:
            parser.error("give an issue number, or --inventory")
        if args.inventory and args.issue is not None:
            parser.error("--inventory takes no issue number (it is a repo-wide report)")
        if args.issue is not None and args.issue <= 0:
            parser.error("issue number must be positive")
        if not args.inventory and (args.dirty_only or args.detached):
            parser.error("--dirty-only/--detached apply only to --inventory")
    except _UsageError:
        return EXIT_USAGE

    try:
        timeout = float(args.timeout)
    except (TypeError, ValueError):
        print("who-is-on: --timeout must be a positive number", file=sys.stderr)
        return EXIT_USAGE
    if not math.isfinite(timeout) or timeout <= 0:
        print("who-is-on: --timeout must be finite and > 0", file=sys.stderr)
        return EXIT_USAGE
    if args.min_keywords < 1:
        print("who-is-on: --min-keywords must be >= 1", file=sys.stderr)
        return EXIT_USAGE

    if args.inventory:
        if args.limit < 1:
            print("who-is-on: --limit must be >= 1", file=sys.stderr)
            return EXIT_USAGE
        repo, code = _resolve_inventory_path(args.repo, args.gh, args.git, timeout)
        if repo is None:
            return code
        try:
            rows, summary = inventory(repo, args.git, timeout, args.limit,
                                      args.dirty_only, args.detached)
        except cp.SurfaceError as exc:
            print(f"who-is-on: {exc}", file=sys.stderr)
            return EXIT_INCOMPLETE
        sys.stdout.write(format_inventory(repo, rows, summary))
        # An unreadable worktree is an INCOMPLETE answer, never a clean one — the
        # safety-critical query must not page "all clear" over a status it could
        # not read. A missing dir is a fully-determined state (the work is gone),
        # so it is reported but does not fail the run.
        if summary["unknown"] or summary["detached_unknown"]:
            return EXIT_INCOMPLETE
        return EXIT_ANSWERED

    try:
        text, code = who_holds(args.issue, args.repo, args.gh, args.git, timeout,
                               args.keywords, args.min_keywords)
    except cp.SurfaceError as exc:
        print(f"who-is-on: {_clean(str(exc))}", file=sys.stderr)
        return EXIT_INCOMPLETE
    except Exception as exc:  # FAIL-CLOSED: never a crash, never 1
        print(f"who-is-on: unexpected failure ({type(exc).__name__}: {_clean(str(exc))}); "
              "reporting an INCOMPLETE answer rather than a crash", file=sys.stderr)
        return EXIT_INCOMPLETE
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
