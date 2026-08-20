#!/usr/bin/env python3
# Historical — uses embedded tortoise.db. Do not run against production Docker.
"""Auto-resolution: map recently closed GitHub issues → resolution-event Points.

    python scripts/resolve-github.py --db tortoise.db --log events.jsonl

Fetches closed issues from the current repo, creates a resolution-event Point
for each, and triggers compute_grounding() to propagate through the graph.

ponytail: gh CLI for issue fetch; no GraphQL API client needed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone  # noqa: F401
from pathlib import Path

# Allow running from repo root or scripts/ dir
_tortoise_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_tortoise_root))

from tortoise.api import EventAPI, provenance  # noqa: E402
from tortoise.log import EventLog  # noqa: E402
from tortoise.projection import FalkorProjection  # noqa: E402


def fetch_closed_issues(limit: int = 20, since: str | None = None
                        ) -> list[dict]:
    """Fetch recently closed GitHub issues via `gh` CLI."""
    fields = ["number", "title", "closedAt", "url"]
    cmd = ["gh", "issue", "list", "--state", "closed",
           "--json", ",".join(fields), "--limit", str(limit)]
    if since:
        cmd.extend(["--search", f"closed:>={since}"])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"gh error: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(
        description="Create resolution-event Points from closed GitHub issues")
    ap.add_argument("--db", type=str, default=None, help="DB path (default: canonical TORTOISE_DB_PATH)")
    ap.add_argument("--log", type=Path, default=Path("events.jsonl"))
    ap.add_argument("--limit", type=int, default=20,
                    help="max issues to fetch (default: 20)")
    ap.add_argument("--since", type=str, default=None,
                    help="only issues closed after this date (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be created, don't write")
    args = ap.parse_args(argv)

    issues = fetch_closed_issues(limit=args.limit, since=args.since)
    if not issues:
        print("No closed issues found.")
        return

    print(f"Found {len(issues)} closed issue(s)")
    if args.dry_run:
        for iss in issues:
            print(f"  #{iss['number']}: {iss['title']}")
        print(f"\n[dry-run] would create {len(issues)} resolution Points → "
              f"{args.log} / {args.db}")
        return

    log = EventLog(args.log)
    from tortoise.config import resolve_db_path
    db_path = resolve_db_path(str(args.db)) if args.db else resolve_db_path()
    proj = FalkorProjection(db_path)
    api = EventAPI(log, initiated_by="user", agent_id="resolve-github",
                   projection=proj)
    try:
        count = 0
        for iss in issues:
            content = f"GitHub #{iss['number']}: {iss['title']}"
            api.add_point(
                content,
                provenance("github-issues", None, None,
                           speaker="system",
                           extracted_by=f"github:{iss.get('url','')}"),
                pointKind="resolution-event",
            )
            count += 1
            print(f"  ✓ #{iss['number']}: {iss['title']}")
    finally:
        proj.close()

    print(f"Created {count} resolution-event Point(s) → {args.db}")


if __name__ == "__main__":
    main()
