#!/usr/bin/env python3
"""Preflight for the Cloudflare Pages upload root (issue #3620).

WHY THIS EXISTS (issue #3620)
-----------------------------
`wrangler pages deploy .` used to upload EVERY file under `website/`, and
`website/.wranglerignore` excluded nothing at all — the Pages upload path uses a
hardcoded `IGNORE_LIST` and reads no ignore file (verified four ways in #3620).
The deploy step now stages a DENYLIST copy and uploads that, which means a NEW
top-level entry under `website/` is staged and served unless it is explicitly
excluded.

The unit-test ratchet that pins this case (`tests/test_pages_bindings.py`)
runs only on the full test selection, and the `deploy` job runs no pytest — so
by itself it is a DETECTOR, not an upload gate: a push adding an unclassified
top-level entry uploads it and only reds afterwards. This script is the missing
gate. It runs in the `deploy` job BEFORE the upload and compares the tracked
top-level entries under `website/` against the reviewed classification in
`config/pages-upload-classification.txt`:

    * an entry on disk but not in the table (a new, unclassified path) FAILS;
    * an entry in the table but no longer on disk (a stale row) FAILS.

So the classification is forced to be updated BEFORE the file can be published,
which is what makes the denylist safe to deploy.

Usage:
    check_pages_upload_root.py [--classification <path>] [--repo <path>]

Exit codes:
    0  every tracked top-level entry under website/ is classified
    1  an unclassified or stale entry — the deploy must not proceed
    2  could not determine the state (bad table, git failure) — fail closed

The comparison is `compare()`, unit-tested in `tests/test_pages_bindings.py`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CLASSIFICATION = REPO / "config" / "pages-upload-classification.txt"

#: The only legal classification rows.
STAGED_VALUES = frozenset({"public", "excluded"})


def load_classification(path: Path = DEFAULT_CLASSIFICATION) -> dict[str, bool]:
    """Parse the reviewed table into ``{top_level_name: staged}``.

    Fails loud on a malformed row or an empty table rather than silently
    comparing against nothing (an empty table would report every entry as
    unclassified, but a typo'd row must not be mistaken for a deliberate one).
    """
    if not path.is_file():
        raise ValueError(f"classification file not found: {path}")
    out: dict[str, bool] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2 or parts[0] not in STAGED_VALUES:
            raise ValueError(
                f"{path}:{lineno}: expected '<public|excluded> <name>', got {raw!r}"
            )
        name = parts[1]
        if name in out:
            raise ValueError(f"{path}:{lineno}: duplicate classification for {name!r}")
        out[name] = parts[0] == "public"
    if not out:
        raise ValueError(f"{path}: no classification rows — refusing a vacuous gate")
    return out


def tracked_top_level(repo: Path = REPO) -> set[str]:
    """The top-level entry under ``website/`` of every tracked file.

    ``git ls-files`` (tracked, i.e. what the deploy could ship from the repo),
    not a directory walk — an untracked local artifact is not part of the
    deployed tree and must not red the gate.
    """
    result = subprocess.run(
        ["git", "ls-files", "website"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        p.split("/", 1)[1].split("/", 1)[0]
        for p in result.stdout.split()
        if p.startswith("website/")
    }


def compare(tracked: set[str], table: dict[str, bool]) -> tuple[list[str], list[str]]:
    """Return ``(unclassified, stale)``; both empty means the table is complete."""
    return sorted(tracked - set(table)), sorted(set(table) - tracked)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--repo", type=Path, default=REPO)
    args = parser.parse_args(argv)

    try:
        table = load_classification(args.classification)
        tracked = tracked_top_level(args.repo)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"::error::could not verify the Pages upload root: {exc}", file=sys.stderr)
        return 2

    unclassified, stale = compare(tracked, table)
    if unclassified:
        print(
            "::error::new top-level entr"
            f"{'y' if len(unclassified) == 1 else 'ies'} under website/ not classified "
            f"in {args.classification.name}: {', '.join(unclassified)} — the Pages "
            "deploy stages a top-level entry unless it is excluded, so an "
            "unclassified entry would be published (#3620); add it to the table",
            file=sys.stderr,
        )
    if stale:
        print(
            f"::error::stale row(s) in {args.classification.name}: {', '.join(stale)} "
            "no longer exist under website/ — remove them so the table stays the "
            "reviewed set (#3620)",
            file=sys.stderr,
        )
    if unclassified or stale:
        return 1

    print(
        f"OK: every tracked top-level entry under website/ is classified "
        f"({len(table)} entries)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
