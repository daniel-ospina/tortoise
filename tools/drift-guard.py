#!/usr/bin/env python3
"""drift-guard — long-lived branch drift + silent-revert gate (#1531, #4174).

Fails when the current branch has drifted from origin/main: commits present
on main but missing from the branch (the #1531 threshold arm), OR — #4174 —
when the branch's tree SILENTLY REVERTS main's work for paths the branch
never touched. The second arm is the conflict-free revert: main changed a
path since the merge base, the branch still carries the pre-change state,
and a merge of the two would therefore look additive from inside while the
branch is deleting main's work from outside.

WHY THE FETCH IS NOT OPTIONAL (#4174). A worktree that never fetched holds a
STALE `origin/main`. Every read against it is self-consistent — the branch
looks purely additive — so the gate passed green while the branch was
thousands of lines behind the real main (PR #4110). The gate therefore
FETCHES the base before measuring and FAILS CLOSED (exit 2) when the ref
cannot be proven fresh: a possibly-stale read is never a pass.

The check's two arms are independent:
  * threshold arm (BEHIND): commits on main missing from the branch, > max;
  * revert arm (SILENT REVERT): paths main moved since the merge base that
    the branch did not move itself — i.e. main's newer content is absent
    from (or superseded in) the branch's tree. This is the conflict-free
    revert, so it fails REGARDLESS of the commit count, and it reports the
    reverted paths and line counts.

Honors `# noqa: drift-guard` inline annotations? No — this is a
remote-state gate, not a file-content scan. It runs on CI for every PR and
on demand via workflow_dispatch; local runs use the same code path.

Usage:
    python3 tools/drift-guard.py                 # origin/main, max-behind 20
    python3 tools/drift-guard.py --base origin/main --max-behind 20
    DRIFT_MAX_BEHIND=10 python3 tools/drift-guard.py   # env override
    python3 tools/drift-guard.py --json          # machine-readable output
    python3 tools/drift-guard.py --head <ref>    # measure a ref other than HEAD

Exit codes:
    0  no drift and no silent revert — gate green
    1  drift beyond threshold OR a silent revert — gate red
    2  environment error (not a git repo / base not a remote-tracking ref /
       base unreachable / fetch failed — freshness unprovable)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_MAX_BEHIND = 20
# Display help for a long revert list; the JSON always carries every path.
MAX_REPORTED_PATHS = 50


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=False,
    )


def repo_root() -> Path:
    """Repo root via git — robust to worktrees, symlinks, relative __file__."""
    r = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        sys.exit(f"drift-guard: not a git repository: {r.stderr.strip()}")
    return Path(r.stdout.strip())


def _split_remote_ref(root: Path, base: str) -> tuple[str, str] | None:
    """('origin', 'main') for a remote-tracking ref, else None.

    `origin/main` and `refs/remotes/origin/main` are fetchable; `main` or
    `feature/x` are not (a local ref, freshness unprovable by fetching).
    A `<name>/<rest>` is only a remote ref when `<name>` is an actual remote.
    """
    if base.startswith("refs/remotes/"):
        parts = base[len("refs/remotes/"):].split("/", 1)
        return (parts[0], parts[1]) if len(parts) == 2 else None
    if "/" not in base:
        return None
    remote, branch = base.split("/", 1)
    if _git(root, "remote", "get-url", remote).returncode != 0:
        return None
    return remote, branch


def fetch_base(root: Path, base: str) -> tuple[str | None, str]:
    """Bring `base` up to date. Returns (error_or_None, freshness).

    Freshness is "fetched" only when the fetch succeeded. There is NO opt-out:
    a local ref cannot be proven fresh, and a fetch failure is returned as an
    error — never a green (#4174: a possibly-stale read is not a pass).
    """
    remote_ref = _split_remote_ref(root, base)
    if remote_ref is None:
        return (f"base '{base}' is not a remote-tracking ref — the gate cannot "
                f"prove its freshness (pass origin/<branch>); an unproven base "
                f"is not a pass"), "unproven"
    remote, branch = remote_ref
    r = _git(root, "fetch", remote, branch, "--quiet")
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        return (f"could not fetch {remote}/{branch} — the base ref's freshness "
                f"cannot be proven, so this is not a pass "
                f"({detail[0] if detail else 'fetch failed'})"), "unproven"
    return None, "fetched"


def _paths_changed(root: Path, a: str, b: str) -> set[str]:
    """Paths differing between <a> and <b>, NUL-separated (rename-free)."""
    r = _git(root, "diff", "--no-renames", "--name-only", "-z", a, b)
    if r.returncode != 0:
        return set()
    return {p for p in r.stdout.split("\0") if p}


def _numstat(root: Path, a: str, b: str) -> dict[str, tuple[int, int]]:
    """{path: (added, deleted)} for <a>..<b>, NUL-separated (`-z`).

    `--numstat -z` emits one NUL-terminated record per file, fields
    tab-separated: `added\\tdeleted\\tpath`. A binary file uses `-`.
    """
    r = _git(root, "diff", "--no-renames", "--numstat", "-z", a, b)
    out: dict[str, tuple[int, int]] = {}
    if r.returncode != 0:
        return out
    for rec in r.stdout.split("\0"):
        if not rec:
            continue
        parts = rec.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        try:
            n_add = int(added)
            n_del = int(deleted)
        except ValueError:
            n_add = n_del = 0  # binary ('-')
        out[path] = (n_add, n_del)
    return out


def silent_reverts(root: Path, mb: str, base: str, head: str) -> list[dict]:
    """Main's post-merge-base changes the branch never took (#4174).

    A path is a SILENT REVERT when main moved it since the merge base but the
    branch did not — the branch still carries the merge-base state, so main's
    newer content is absent from (or superseded in) the branch's tree. The
    merge of head into base is conflict-free on exactly this class: main's
    advance does not overlap the branch's, and the branch's tree loses it.

    A path the branch ALSO changed is not silent (GitHub surfaces the textual
    conflict); a path main did not move is not a revert at all.
    """
    moved_by_base = _paths_changed(root, mb, base)
    moved_by_head = _paths_changed(root, mb, head)
    reverted = sorted(moved_by_base - moved_by_head)
    if not reverted:
        return []
    counts = _numstat(root, mb, base)
    return [
        {"path": p, "added": counts.get(p, (0, 0))[0], "deleted": counts.get(p, (0, 0))[1]}
        for p in reverted
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main",
                    help="base ref to compare against (default: origin/main)")
    ap.add_argument("--head", default="HEAD",
                    help="ref to measure (default: HEAD); the gate measures the "
                         "PR HEAD, never the synthetic merge ref, which contains "
                         "the base by construction")
    ap.add_argument("--max-behind", type=int, default=None,
                    help="fail when behind by more than N commits "  # noqa: UP031
                         "(default: %d)" % DEFAULT_MAX_BEHIND)
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output")
    args = ap.parse_args()

    max_behind = args.max_behind
    if max_behind is None:
        try:
            max_behind = int(os.environ.get("DRIFT_MAX_BEHIND", ""))
        except ValueError:
            max_behind = DEFAULT_MAX_BEHIND

    root = repo_root()

    def emit(payload: dict, text_lines: list[str], rc: int) -> int:
        if args.json:
            print(json.dumps(payload))
        else:
            for line in text_lines:
                print(line, file=sys.stderr if rc != 0 else sys.stdout)
        return rc

    # ── freshness first: a possibly-stale ref is never a pass (#4174) ──────
    fetch_err, freshness = fetch_base(root, args.base)
    if fetch_err is not None:
        payload = {"status": "error", "reason": fetch_err,
                   "base": args.base, "freshness": freshness}
        return emit(payload, [f"ERROR drift-guard: {fetch_err}"], 2)

    branch = _git(root, "rev-parse", "--abbrev-ref", args.head).stdout.strip()
    if not branch or branch == "HEAD":
        branch = args.head if args.head != "HEAD" else "(detached HEAD)"

    # Base must be resolvable — a wrong/missing ref is an environment error,
    # not a clean gate.
    base_ok = _git(root, "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}")
    if base_ok.returncode != 0:
        msg = (f"drift-guard: base ref '{args.base}' not resolvable "
               f"(fetch-depth 0 required in CI); cannot compare")
        return emit({"status": "error", "reason": msg, "base": args.base,
                     "freshness": freshness},
                    [f"ERROR {msg}"], 2)

    head_ok = _git(root, "rev-parse", "--verify", "--quiet", f"{args.head}^{{commit}}")
    if head_ok.returncode != 0:
        msg = f"drift-guard: head ref '{args.head}' not resolvable; cannot measure"
        return emit({"status": "error", "reason": msg, "base": args.base,
                     "freshness": freshness},
                    [f"ERROR {msg}"], 2)

    ahead = _git(root, "rev-list", "--count", f"{args.base}..{args.head}").stdout.strip()
    behind = _git(root, "rev-list", "--count", f"{args.head}..{args.base}").stdout.strip()
    try:
        ahead_n, behind_n = int(ahead), int(behind)
    except ValueError:  # pragma: no cover — git always prints ints
        print("drift-guard: failed to count commits", file=sys.stderr)
        return 2

    # ── silent-revert arm (#4174) ─────────────────────────────────────────
    reverts: list[dict] = []
    mb = _git(root, "merge-base", args.base, args.head).stdout.strip()
    if mb and behind_n > 0:
        reverts = silent_reverts(root, mb, args.base, args.head)
    revert_lines = sum(r["added"] + r["deleted"] for r in reverts)

    drifted = behind_n > max_behind
    status = "drift" if (drifted or reverts) else "ok"
    report = {
        "status": status,
        "branch": branch,
        "base": args.base,
        "freshness": freshness,
        "ahead": ahead_n,
        "behind": behind_n,
        "max_behind": max_behind,
        "merge_base": mb or None,
        "reverts": reverts,
        "revert_files": len(reverts),
        "revert_lines": revert_lines,
    }

    if status == "ok":
        return emit(
            report,
            [f"OK  {branch}: {behind_n} behind {args.base} "
             f"(<= {max_behind}), {ahead_n} ahead (base {freshness}) — gate green"],
            0,
        )

    lines: list[str] = []
    if drifted:
        lines.append(
            f"FAIL {branch}: {behind_n} behind {args.base} "
            f"(> {max_behind} max) — branch drifted; fetch and reconcile onto "
            f"{args.base} before this lands (epic #1509 P3: every "
            f"real-backend E2E gates on 'worktree == origin/main')")
    if reverts:
        lines.append(
            f"FAIL {branch}: SILENTLY REVERTING {len(reverts)} path(s) that "
            f"{args.base} changed and this branch never took "
            f"(base {freshness}, merge-base {mb[:12]}):")
        for r in reverts[:MAX_REPORTED_PATHS]:
            lines.append(f"    {r['path']}  +{r['added']}/-{r['deleted']}")
        if len(reverts) > MAX_REPORTED_PATHS:
            lines.append(f"    … and {len(reverts) - MAX_REPORTED_PATHS} more")
        lines.append(
            f"    ({len(reverts)} file(s), {revert_lines} line(s)) — the "
            f"conflict-free revert: from inside the branch this is invisible, "
            f"from outside it deletes {args.base}'s newer work. Fetch and "
            f"reconcile before this lands (#4174).")
    return emit(report, lines, 1)


if __name__ == "__main__":
    sys.exit(main())
