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
  2. an open PR for this headRefName             -> PRESERVE ``open-pr``
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
* **TOCTOU.** The ref OID is re-verified immediately before each delete; a branch
  moved between classification and deletion is skipped, not destroyed.
* **Recovery.** Every deleted branch, its tip SHA and its verdict is written to
  the report BEFORE the delete phase; ``--backup-bundle`` optionally writes one
  ``git bundle`` of the deleted tips (no ``refs/reaped/*`` refs — those would
  collide with the worktree engine's ``refs/heads/*``-only survival doctrine).
* **Guard.** ``--apply`` refuses to run from the MAIN checkout: this tool invokes
  ``git branch -D`` from an interpreter file payload, which the
  ``main-worktree-guard`` extension does not content-gate, so the tool declares
  and enforces the run-from-a-worktree contract itself.
* Remote branches are NEVER touched.

Exit codes (a delegate code is never passed through unmodified)
-------------------------------------------------------------
  0  complete (dry-run or apply)
  2  INCOMPLETE — a surface was truncated/unqueryable, or the worktree engine
     was missing/unrunnable. Nothing was deleted.
  3  usage error, or ``--apply`` refused from the main checkout
  4  partial failure — at least one deletion was refused, or the worktree
     engine reported >=1 FAILED removal
  5  internal error (unexpected engine exit, malformed output)

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
import fnmatch
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_INCOMPLETE = 2
EXIT_USAGE = 3
EXIT_PARTIAL = 4
EXIT_INTERNAL = 5

#: Page caps. A list that reaches its cap is TRUNCATED, never "complete but short".
DEFAULT_MAX_PR_PAGES = 50
DEFAULT_PER_PAGE = 100

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
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )


def _gh_bin() -> str:
    return os.environ.get("BRANCH_REAPER_GH", "gh")


def _now() -> int:
    raw = os.environ.get("BRANCH_REAPER_NOW")
    if raw:
        return int(raw)
    import time

    return int(time.time())


# ── repo / target resolution ────────────────────────────────────────────────

def resolve_repo(target: str | None) -> tuple[str, str | None]:
    """Return ``(repo_root, slug_or_None)`` for a path or ``owner/name`` target."""
    target = target or os.getcwd()
    if os.path.isdir(target):
        root = _run(["git", "-C", target, "rev-parse", "--show-toplevel"])
        if root.returncode != 0:
            raise SystemExit(f"branch_reaper: {target!r} is not inside a git repository")
        return os.path.realpath(root.stdout.strip()), _slug_from_remote(target)
    # Not a directory: treat as owner/name.
    if "/" in target:
        return os.getcwd(), target
    raise SystemExit(f"branch_reaper: --repo {target!r} is not a directory or owner/name")


def _slug_from_remote(repo_root: str) -> str | None:
    remote = _run(["git", "-C", repo_root, "remote", "get-url", "origin"])
    if remote.returncode != 0:
        return None
    url = remote.stdout.strip()
    if url.endswith(".git"):
        url = url[:-4]
    if "github.com" in url:
        tail = url.split("github.com", 1)[1].lstrip(":/")
        parts = tail.split("/")
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
    res = _run(["git", "-C", repo_root, "rev-parse", "--abbrev-ref", "HEAD"])
    name = res.stdout.strip()
    return name if res.returncode == 0 and name and name != "HEAD" else None


# ── enumeration ─────────────────────────────────────────────────────────────

def enum_branches(repo_root: str) -> dict[str, dict]:
    """name -> {oid, ts} for every local branch."""
    fmt = "%(refname:short)%09%(objectname)%09%(committerdate:unix)"
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
                "--format=%(refname:short)", "refs/heads"])
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
        try:
            pages = json.loads(res.stdout) if res.stdout.strip() else []
        except json.JSONDecodeError as exc:
            raise Incomplete(f"gh pr enumeration ({state}) returned malformed JSON: {exc}") from exc
        if not isinstance(pages, list):
            raise Incomplete(f"gh pr enumeration ({state}) returned {type(pages).__name__}, expected list")
        if len(pages) >= max_pages:
            raise Incomplete(
                f"gh pr enumeration ({state}) hit the {max_pages}-page cap — TRUNCATED")
        return pages

    open_pages = _fetch("open")
    closed_pages = _fetch("closed")

    open_by_head: dict[str, list[dict]] = {}
    merged_by_head: dict[str, list[dict]] = {}
    closed_unmerged_by_head: dict[str, list[dict]] = {}
    for pages, dest in ((open_pages, open_by_head), (closed_pages, None)):
        for page in pages:
            for pr in page:
                head = (pr.get("head") or {}).get("ref")
                if not head:
                    continue
                row = {"number": pr.get("number"),
                       "sha": (pr.get("head") or {}).get("sha"),
                       "merged": pr.get("merged_at") is not None}
                if dest is not None:
                    dest.setdefault(head, []).append(row)
                elif row["merged"]:
                    merged_by_head.setdefault(head, []).append(row)
                else:
                    closed_unmerged_by_head.setdefault(head, []).append(row)
    return {"open": open_by_head, "merged": merged_by_head,
            "closed_unmerged": closed_unmerged_by_head}


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


# ── reporting ───────────────────────────────────────────────────────────────

def _fmt_ts(ts: int) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")


def build_report(rows: list[dict], worktrees: list[dict], ancestors: set[str],
                 *, repo_root: str, slug: str | None, main_ref_used: str,
                 include_closed_unmerged: bool, engine_output: str | None = None,
                 apply_results: list[dict] | None = None, now: int | None = None,
                 disk: dict | None = None) -> str:
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
        out.append(f"| `{r['branch']}` | `{r['oid'][:12]}` | {age} | {r['reason']} | "
                   f"{r['commits_survive']} | {r['pr_number'] or '—'} |")
    out.append("")

    if held_safe:
        out.append("## Safe by history — held by a worktree (branch preserved)\n")
        out.append("The delegate preserves these checkouts (dirty / ignored-artifact / too-recent), "
                   "or they were not torn down; the branch is kept. Reported, not deleted.\n")
        out.append("| branch | worktree | verdict | PR |")
        out.append("|---|---|---|---|")
        for r in sorted(held_safe, key=lambda r: r["ts"]):
            out.append(f"| `{r['branch']}` | `{r['worktree']}` | {r['reason']} | {r['pr_number'] or '—'} |")
        out.append("")

    out.append("## Judgement — no PR, not an ancestor (never auto-deleted)\n")
    out.append("Oldest first. A human decides.\n")
    out.append("| branch | tip | age (days) |")
    out.append("|---|---|---|")
    for r in judgement:
        age = (now - r["ts"]) // 86400
        out.append(f"| `{r['branch']}` | `{r['oid'][:12]}` | {age} |")
    out.append("")

    if detached:
        out.append("## Detached-HEAD worktrees (never auto-deleted)\n")
        out.append("| path | HEAD | age (days) |")
        out.append("|---|---|---|")
        for wt in sorted(detached, key=lambda w: w.get("head") or ""):
            out.append(f"| `{wt['path']}` | `{(wt.get('head') or '?')[:12]}` | — |")
        out.append("")

    if engine_output is not None:
        out.append("## Delegated worktree engine (`pi-reap-worktrees.sh`)\n")
        out.append("```")
        out.append(engine_output.strip()[-4000:])
        out.append("```")
        out.append("")

    if apply_results is not None:
        deleted = [r for r in apply_results if r["result"] == "deleted"]
        refused = [r for r in apply_results if r["result"] == "refused"]
        skipped = [r for r in apply_results if r["result"] == "skipped"]
        out.append("## Post-apply results\n")
        out.append("| metric | value |")
        out.append("|---|---|")
        out.append(f"| deleted | {len(deleted)} |")
        out.append(f"| refused | {len(refused)} |")
        out.append(f"| skipped (held / moved) | {len(skipped)} |")
        if disk:
            out.append(f"| filesystem free before | {disk.get('before_kb')} KiB |")
            out.append(f"| filesystem free after | {disk.get('after_kb')} KiB |")
            out.append(f"| free-space delta | {disk.get('delta_kb')} KiB |")
        out.append("")
        out.append("### Recovery record (written before deletion)\n")
        out.append("| branch | tip | how |")
        out.append("|---|---|---|")
        for r in deleted:
            out.append(f"| `{r['branch']}` | `{r['oid']}` | reflog ~30d / `--backup-bundle` |")
        out.append("")
        if refused:
            out.append("### Refused\n")
            for r in refused:
                out.append(f"- `{r['branch']}` — {r.get('detail', 'refused')}")
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


def delete_branches(repo_root: str, rows: list[dict], held: set[str],
                    *, backup_bundle: str | None) -> list[dict]:
    results: list[dict] = []
    targets = [r for r in rows if r["verdict"] == VERDICT_SAFE]
    if backup_bundle:
        shas = [r["oid"] for r in targets if r["branch"] not in held]
        if shas:
            bundle = _run(["git", "-C", repo_root, "bundle", "create", backup_bundle, *shas],
                          timeout=600)
            if bundle.returncode != 0:
                results.append({"branch": "(bundle)", "oid": "", "result": "skipped",
                                "detail": f"bundle failed: {bundle.stderr.strip()}"})
    for r in targets:
        b, expected = r["branch"], r["oid"]
        if b in held:
            results.append({"branch": b, "oid": expected, "result": "skipped",
                            "detail": "worktree-held"})
            continue
        current = _run(["git", "-C", repo_root, "rev-parse", "--verify", f"refs/heads/{b}"])
        if current.returncode != 0 or current.stdout.strip() != expected:
            results.append({"branch": b, "oid": expected, "result": "skipped",
                            "detail": "ref moved between classify and delete"})
            continue
        # `git branch -D` itself refuses a branch checked out in ANY worktree.
        res = _run(["git", "-C", repo_root, "branch", "-D", b])
        if res.returncode == 0:
            results.append({"branch": b, "oid": expected, "result": "deleted", "detail": ""})
        else:
            results.append({"branch": b, "oid": expected, "result": "refused",
                            "detail": (res.stderr or res.stdout).strip()})
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
                   help="write one git bundle of the deleted tips before deleting")
    p.add_argument("--max-pr-pages", type=int, default=DEFAULT_MAX_PR_PAGES)
    p.add_argument("--engine-timeout", type=int, default=900)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        repo_root, slug = resolve_repo(args.repo)
    except SystemExit as exc:
        return int(str(exc.code)) if str(exc.code).isdigit() else EXIT_USAGE
    if args.slug:
        slug = args.slug

    if args.apply and is_main_checkout(repo_root):
        print("branch_reaper: refusing --apply from the MAIN checkout — run from a linked "
              "worktree (this tool invokes `git branch -D` as an interpreter file payload, "
              "which main-worktree-guard does not content-gate).", file=sys.stderr)
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

    result = EXIT_OK
    engine_output: str | None = None
    apply_results: list[dict] | None = None
    disk: dict | None = None

    if args.apply:
        if args.reap_worktrees:
            engine = args.worktree_engine or os.path.join(repo_root, "scripts", "pi-reap-worktrees.sh")
            try:
                rc, engine_output = run_worktree_engine(engine, repo_root, args.engine_timeout)
            except Incomplete as exc:
                print(f"branch_reaper: INCOMPLETE — {exc}. Nothing was deleted.", file=sys.stderr)
                return EXIT_INCOMPLETE
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
            worktrees = enum_worktrees(repo_root)

        held = {wt["branch"] for wt in worktrees if wt.get("branch")}
        # Pre-delete recovery record: write the report BEFORE anything is removed.
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(build_report(
                rows, worktrees, ancestors, repo_root=repo_root, slug=slug,
                main_ref_used=ref, include_closed_unmerged=args.include_closed_unmerged,
                engine_output=engine_output))
        free_before = _disk_free_kb(repo_root)
        apply_results = delete_branches(repo_root, rows, held, backup_bundle=args.backup_bundle)
        free_after = _disk_free_kb(repo_root)
        if free_before is not None and free_after is not None:
            disk = {"before_kb": free_before, "after_kb": free_after,
                    "delta_kb": free_after - free_before}
        if any(r["result"] == "refused" for r in apply_results):
            result = EXIT_PARTIAL

    report = build_report(rows, worktrees, ancestors, repo_root=repo_root, slug=slug,
                          main_ref_used=ref,
                          include_closed_unmerged=args.include_closed_unmerged,
                          engine_output=engine_output, apply_results=apply_results, disk=disk)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report)
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
                  f"refused={sum(1 for r in apply_results if r['result'] == 'refused')} "
                  f"skipped={sum(1 for r in apply_results if r['result'] == 'skipped')}")
        if args.report:
            print(f"  report: {args.report}")
    return result


def _main_wt(repo_root: str) -> str:
    listing = _run(["git", "-C", repo_root, "worktree", "list", "--porcelain"])
    for line in listing.stdout.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):]
    return repo_root


if __name__ == "__main__":
    sys.exit(main())
