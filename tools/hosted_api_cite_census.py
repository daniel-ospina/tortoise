#!/usr/bin/env python3
"""Measure the `hosted_api.py:<line>` citation population — the two notes' census.

`tortoise/activation_scorecard.py` and `tortoise/hosted_api.py` each carry a note
stating how many places cite `hosted_api.py` by absolute line number, because that
number is what justifies interpolating a query instead of binding parameters (the
interpolation buys LINE COST: only cites ABOVE the pivot shift when `hosted_api.py`
grows). A hand count of it has been wrong in BOTH directions at one head, so the
census has one command:

    uv run python tools/hosted_api_cite_census.py            # human-readable
    uv run python tools/hosted_api_cite_census.py --json     # for a script
    uv run python tools/hosted_api_cite_census.py --pivot 23128

Print the values and paste them into the two notes. Do NOT assert these numbers in
a fast-suite test: the population belongs to EVERY tracked file, so a test that
compared it to prose would red unrelated PRs (measured: ~1 commit in 30 adds a
`hosted_api.py:<N>` citation). The test in
`tests/test_demo_session_read_exclusion_8290.py` pins only what this PR own: that
the two notes agree with each other and with the arithmetic, and that the pivot
cite really is the `LIFETIME_MEMORY_QUERY` execution site.

⛔ The pattern requires DIGITS (`[0-9]+`, not `[0-9]*`). The `*` form also matches a
bare `hosted_api.py:` and `sort -u` then counts that empty run as one more target —
that is exactly how the count was briefly "re-measured" from 449 to 450.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: The one pattern that defines a "cite target". DIGITS REQUIRED — see the module
#: docstring for the `*` footgun.
CITE_RE = r"hosted_api\.py:[0-9]+"

#: The line the two notes are written around: the ONE production execution site of
#: `LIFETIME_MEMORY_QUERY`.
DEFAULT_PIVOT = 23128


def census(pivot: int = DEFAULT_PIVOT, repo: Path = REPO) -> dict:
    """The four figures the notes state, plus the pivot split."""
    proc = subprocess.run(
        ["git", "grep", "-o", "-h", "-E", CITE_RE],
        cwd=str(repo), capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"hosted_api_cite_census: `git grep` failed in {repo}: {proc.stderr!r}")
    targets = sorted({int(tok.rsplit(":", 1)[1]) for tok in proc.stdout.split()})

    files = subprocess.run(
        ["git", "grep", "-l", "-E", CITE_RE],
        cwd=str(repo), capture_output=True, text=True, check=False,
    )
    if files.returncode != 0:
        raise SystemExit(
            f"hosted_api_cite_census: `git grep -l` failed: {files.stderr!r}")

    return {
        "pivot": pivot,
        "distinct_targets": len(targets),
        "below_pivot": sum(1 for n in targets if n < pivot),
        "above_pivot": sum(1 for n in targets if n > pivot),
        "at_pivot": sum(1 for n in targets if n == pivot),
        "citing_files": len([ln for ln in files.stdout.splitlines() if ln.strip()]),
        "min_target": targets[0] if targets else None,
        "max_above": max((n for n in targets if n > pivot), default=None),
    }


def pivot_is_the_lifetime_site(pivot: int = DEFAULT_PIVOT, repo: Path = REPO) -> bool:
    """True when `hosted_api.py:<pivot>` really executes `LIFETIME_MEMORY_QUERY`."""
    path = repo / "tortoise" / "hosted_api.py"
    lines = path.read_text(encoding="utf-8").splitlines()
    if pivot > len(lines):
        return False
    # The execution site is the query call; the anchor need not sit on the exact
    # line, so accept a small window around it.
    window = "\n".join(lines[max(0, pivot - 4): pivot + 4])
    return "LIFETIME_MEMORY_QUERY" in window


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pivot", type=int, default=DEFAULT_PIVOT)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    data = census(args.pivot)
    data["pivot_is_lifetime_site"] = pivot_is_the_lifetime_site(args.pivot)

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0

    print(f"hosted_api.py cite census (pivot {data['pivot']}):")
    print(f"  distinct targets   {data['distinct_targets']}")
    print(f"  below the pivot    {data['below_pivot']}  ({data['min_target']} … )")
    print(f"  above the pivot    {data['above_pivot']}  ( … {data['max_above']})")
    print(f"  at the pivot       {data['at_pivot']}")
    print(f"  citing files       {data['citing_files']}")
    print(f"  pivot is the LIFETIME_MEMORY_QUERY site: "
          f"{data['pivot_is_lifetime_site']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
