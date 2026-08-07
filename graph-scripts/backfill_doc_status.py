#!/usr/bin/env python3
"""Backfill doc_status on existing Documents (#133).

Stamps all Documents without a doc_status property to 'captured' so the
state machine is consistent. Idempotent — safe to re-run.

Usage:
    python3 graph-scripts/backfill_doc_status.py [--dry-run] [--graph GRAPH] [--uri URI]

Defaults to TORTOISE_DB_URI env var (or docker://:falkordb@localhost:16379/tortoise).

Test safety: always verify graph name before running. For tests, use
test-prefixed graphs (tortoise_test_*) and pass --yes to skip confirmation.
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow running from worktree root or graph-scripts/ dir
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

DEFAULT_URI = "docker://:falkordb@localhost:16379/tortoise"


def test_guard(graph_name: str, yes: bool = False) -> None:
    """Safety gate: confirm before running on non-test graphs."""
    if graph_name.startswith("tortoise_test_") or graph_name.startswith("test_"):
        print(f"✅ Test graph detected ({graph_name}) — proceeding")
        return
    if yes:
        print(f"⚠️  Production graph ({graph_name}) — --yes flag set, proceeding")
        return
    print(f"\n⚠️  Target graph is '{graph_name}' — NOT a test graph.")
    print("    This script stamps ALL Documents without doc_status as 'captured'.")
    print("    Run with --yes to confirm, or use a test-prefixed graph.")
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="#133: Backfill doc_status on existing Documents")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only, no writes")
    ap.add_argument("--yes", action="store_true",
                    help="skip confirmation (required for non-test graphs)")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", DEFAULT_URI))
    ap.add_argument("--graph", default="tortoise")
    args = ap.parse_args()

    # Resolve graph name from URI
    uri = args.uri
    if uri.startswith("docker://"):
        rest = uri[len("docker://"):]
        _, hostport = rest.split("@", 1)
        host, _, portpath = hostport.rpartition(":")
        port, _, graph_from_path = portpath.partition("/")
        graph_name = graph_from_path or args.graph
    else:
        print(f"Unsupported URI scheme: {uri}")
        return 1

    test_guard(graph_name, args.yes)

    # Connect via FalkorProjection
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(uri)
    try:
        # Count documents missing doc_status
        count_rows = proj.g.query(
            "MATCH (d:Document) "
            "WHERE d.doc_status IS NULL "
            "RETURN count(d) AS cnt"
        ).result_set
        total = count_rows[0][0] if count_rows else 0

        if total == 0:
            print("No Documents without doc_status — nothing to backfill")
            return 0

        if args.dry_run:
            print(f"[dry-run] Would stamp {total} Document(s) → doc_status='captured'")
            # Show a sample
            sample = proj.g.query(
                "MATCH (d:Document) WHERE d.doc_status IS NULL "
                "RETURN d.id LIMIT 5"
            ).result_set
            for row in sample:
                print(f"  - {row[0]}")
            if total > 5:
                print(f"  ... and {total - 5} more")
            return 0

        # Idempotent: SET where missing, skip already-set
        result = proj.g.query(
            "MATCH (d:Document) "
            "WHERE d.doc_status IS NULL "
            "SET d.doc_status = 'captured' "
            "RETURN count(d) AS updated"
        )
        updated = result.result_set[0][0] if result.result_set else 0
        print(f"Backfill complete: {updated} Document(s) stamped → doc_status='captured' "
              f"(graph: {graph_name})")
        return 0
    finally:
        proj.close()


if __name__ == "__main__":
    sys.exit(main())
