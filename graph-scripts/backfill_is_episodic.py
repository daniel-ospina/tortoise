#!/usr/bin/env python3
"""Backfill `is_episodic: true` on legacy regex-path capture nodes (#947, epic #909 §4.4, R-18).

Pre-#947 regex-path captures (sdk.capture_session / hosted POST /v1/sessions)
wrote Session/Event/Source/Point nodes WITHOUT the `is_episodic` flag. The
post-fix points quota counts non-episodic Points only, and a MISSING flag
counts as non-episodic (fail-closed) — so legacy captures over-count → false
402s persist for the teams that already use capture (R-18). This one-query
Cypher migration stamps `is_episodic: true` on every Session/Event/Source/
Point node that lacks the flag.

Idempotent — safe to re-run (only touches nodes with is_episodic IS NULL).

Usage:
    python3 graph-scripts/backfill_is_episodic.py [--dry-run] [--graph GRAPH] [--uri URI] [--yes]

Defaults to TORTOISE_DB_URI env var (or docker://:falkordb@localhost:16379/tortoise).
Hosted multi-tenant: run once per tenant graph (--graph team_<team_id>).
Local embedded (--uri <path to embedded.db>, or TORTOISE_DB_PATH): runs on
the graph named by --graph (default "tortoise").

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

# The one-query Cypher migration (R-18): stamp every capture-path node that
# lacks the flag. Counters (Session/Event/Source) are episodic by definition
# (§4.4 amendment #13); legacy regex-path Points are episodic too.
BACKFILL_QUERY = (
    "MATCH (n) "
    "WHERE (n:Session OR n:Event OR n:Source OR n:Point) "
    "  AND n.is_episodic IS NULL "
    "SET n.is_episodic = true "
    "RETURN count(n) AS updated"
)


def run_backfill(proj, *, dry_run: bool = False) -> dict:
    """Run the one-query is_episodic backfill on a FalkorProjection.

    Returns {"matched": int, "updated": int} (dry_run reports matched, writes
    nothing). Importable so unit tests can drive the migration directly on an
    embedded test graph (tests/test_quota.py legacy fixture, DE2E-7/R-18).
    """
    count_rows = proj.g.query(
        "MATCH (n) "
        "WHERE (n:Session OR n:Event OR n:Source OR n:Point) "
        "  AND n.is_episodic IS NULL "
        "RETURN count(n) AS cnt"
    ).result_set
    matched = int(count_rows[0][0]) if count_rows and count_rows[0][0] else 0
    if dry_run or matched == 0:
        return {"matched": matched, "updated": 0}
    result = proj.g.query(BACKFILL_QUERY)
    updated = int(result.result_set[0][0]) if result.result_set else 0
    return {"matched": matched, "updated": updated}


def test_guard(graph_name: str, yes: bool = False) -> None:
    """Safety gate: confirm before running on non-test graphs."""
    if graph_name.startswith("tortoise_test_") or graph_name.startswith("test_"):
        print(f"✅ Test graph detected ({graph_name}) — proceeding")
        return
    if yes:
        print(f"⚠️  Production graph ({graph_name}) — --yes flag set, proceeding")
        return
    print(f"\n⚠️  Target graph is '{graph_name}' — NOT a test graph.")
    print("    This script stamps ALL Session/Event/Source/Point nodes without")
    print("    is_episodic as is_episodic=true.")
    print("    Run with --yes to confirm, or use a test-prefixed graph.")
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="#947: Backfill is_episodic on legacy capture nodes")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only, no writes")
    ap.add_argument("--yes", action="store_true",
                    help="skip confirmation (required for non-test graphs)")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", DEFAULT_URI))
    ap.add_argument("--graph", default="tortoise",
                    help="graph name (hosted: per-tenant graph, e.g. team_<id>)")
    args = ap.parse_args()

    uri = args.uri
    if uri.startswith(("docker://", "redis://", "rediss://")):
        # FalkorDB (docker URI / cloud): graph name from --graph or URI path
        from urllib.parse import urlparse
        graph_name = args.graph
        if graph_name == "tortoise":
            graph_name = urlparse(uri).path.lstrip("/") or "tortoise"
        test_guard(graph_name, args.yes)
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection.from_uri(uri, graph_name=graph_name)
    else:
        # Local embedded db path (redislite) — TORTOISE_DB_URI absent
        if uri == DEFAULT_URI:
            from tortoise.config import resolve_db_path
            uri = resolve_db_path()
        graph_name = args.graph
        test_guard(graph_name, args.yes)
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection(uri, graph_name=graph_name)

    try:
        report = run_backfill(proj, dry_run=args.dry_run)
        if args.dry_run:
            print(f"[dry-run] Would stamp {report['matched']} node(s) "
                  f"→ is_episodic=true (graph: {graph_name})")
        elif report["updated"] == 0:
            print(f"No Session/Event/Source/Point nodes missing is_episodic — "
                  f"nothing to backfill (graph: {graph_name})")
        else:
            print(f"Backfill complete: {report['updated']} node(s) stamped "
                  f"→ is_episodic=true (graph: {graph_name})")
        return 0
    finally:
        proj.close()


if __name__ == "__main__":
    sys.exit(main())
