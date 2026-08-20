#!/usr/bin/env python3
"""drift-guard — long-lived branch drift gate (epic #1509 P3 / issue #1531).

Fails when the current branch has drifted from origin/main beyond a
threshold (commits present on main but missing from the branch). The v3
epic's real-backend E2Es all gate on 'worktree == origin/main' — a
long-lived branch silently diverging from main means the shipped system is
not the tested system. Make the drift loud instead.

The check is BEHIND-only: commits ahead of main are normal feature work and
do not fail. Only work missing from main (branch behind by > max_behind)
fails — a branch rebased on main reports behind=0 regardless of how many
commits it adds.

Honors `# noqa: drift-guard` inline annotations? No — this is a
remote-state gate, not a file-content scan. It runs on CI for every PR and
on demand via workflow_dispatch; local runs use the same code path.

Usage:
    python3 tools/drift-guard.py                 # origin/main, max-behind 20
    python3 tools/drift-guard.py --base origin/main --max-behind 20
    DRIFT_MAX_BEHIND=10 python3 tools/drift-guard.py   # env override
    python3 tools/drift-guard.py --json          # machine-readable output

Exit codes:
    0  no drift (behind <= max_behind) — gate green
    1  drift beyond threshold — gate red
    2  environment error (not a git repo / base unreachable)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_MAX_BEHIND = 20


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main",
                    help="base ref to compare against (default: origin/main)")
    ap.add_argument("--max-behind", type=int, default=None,
                    help="fail when behind by more than N commits "
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
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch == "HEAD":
        branch = "(detached HEAD)"

    # Base must be resolvable — a wrong/missing ref is an environment error,
    # not a clean gate.
    base_ok = _git(root, "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}")
    if base_ok.returncode != 0:
        msg = (f"drift-guard: base ref '{args.base}' not resolvable "
               f"(fetch-depth 0 required in CI); cannot compare")
        if args.json:
            print(json.dumps({"status": "error", "reason": msg}))
        else:
            print(f"ERROR {msg}", file=sys.stderr)
        return 2

    ahead = _git(root, "rev-list", "--count", f"{args.base}..HEAD").stdout.strip()
    behind = _git(root, "rev-list", "--count", f"HEAD..{args.base}").stdout.strip()
    try:
        ahead_n, behind_n = int(ahead), int(behind)
    except ValueError:  # pragma: no cover — git always prints ints
        print("drift-guard: failed to count commits", file=sys.stderr)
        return 2

    report = {
        "status": "ok" if behind_n <= max_behind else "drift",
        "branch": branch,
        "base": args.base,
        "ahead": ahead_n,
        "behind": behind_n,
        "max_behind": max_behind,
    }

    if args.json:
        print(json.dumps(report))
        return 0 if behind_n <= max_behind else 1

    if behind_n <= max_behind:
        print(f"OK  {branch}: {behind_n} behind {args.base} "
              f"(<= {max_behind}), {ahead_n} ahead — gate green")
        return 0

    print(f"FAIL {branch}: {behind_n} behind {args.base} "
          f"(> {max_behind} max) — branch drifted; rebase onto {args.base} "
          f"before this lands (epic #1509 P3: every real-backend E2E gates "
          f"on 'worktree == origin/main')")
    return 1


if __name__ == "__main__":
    sys.exit(main())
