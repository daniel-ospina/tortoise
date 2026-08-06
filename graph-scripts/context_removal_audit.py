#!/usr/bin/env python3
"""Context removal audit sidecar — #49 Phase 2 safety net (Task 2.0).

Per plan §9.7: Write data/migrations/2026-08-06_context_removal_audit.json
BEFORE the REMOVE migration. This is the permanent record of which points
carried which context values — the ONLY way to answer "what was in the X subgraph"
after Phase 2 removes n.context.

Usage:
  TORTOISE_DB_URI=docker://:@localhost:16379/tortoise \\
    python3 graph-scripts/context_removal_audit.py

  # Dry-run (print what would be written):
  python3 graph-scripts/context_removal_audit.py --dry-run

  # Custom output path:
  python3 graph-scripts/context_removal_audit.py --output /tmp/audit.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

# Allow running from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers ────────────────────────────────────────────────────────────

def _parse_uri(uri: str) -> dict:
    """Parse docker:// URI into components."""
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 16379,
        "password": parsed.password or "",
        "graph": parsed.path.lstrip("/") or "tortoise",
    }


def collect_context_map(g) -> dict[str, list[str]]:
    """Build {context_value: [point_ids]} for all Points with context.

    This is the full inventory BEFORE the REMOVE migration.
    Batched by context to avoid server-side query timeout on the full scan
    (1005 distinct contexts / 4618 points).
    """
    ctx_rows = g.query(
        "MATCH (n:Point) WHERE n.context IS NOT NULL "
        "RETURN DISTINCT n.context"
    ).result_set
    ctx_map: dict[str, list[str]] = defaultdict(list)
    for (ctx_val,) in ctx_rows:
        id_rows = g.query(
            "MATCH (n:Point {context:$ctx}) RETURN n.id",
            params={"ctx": ctx_val},
        ).result_set
        ctx_map[ctx_val] = [r[0] for r in id_rows]
    return dict(ctx_map)


def collect_context_details(g) -> dict:
    """Collect additional metadata about context values.

    Returns {context_value: {count, kinds, has_operators}}
    """
    ctx_details = {}
    # Get all contexts with point counts
    count_rows = g.query(
        "MATCH (n:Point) WHERE n.context IS NOT NULL "
        "RETURN n.context, count(n), collect(DISTINCT n.pointKind) "
        "ORDER BY n.context"
    ).result_set
    for ctx_val, cnt, kinds in count_rows:
        # Check if context has operators
        op_rows = g.query(
            "MATCH (op:Point {is_operator:true})-[r:IMPL|NAND]->(c:Point {context:$ctx}) "
            "RETURN count(DISTINCT op)",
            params={"ctx": ctx_val},
        ).result_set
        op_count = op_rows[0][0] if op_rows else 0
        ctx_details[ctx_val] = {
            "point_count": cnt,
            "point_kinds": list(kinds) if kinds else [],
            "connected_operators": op_count,
        }
    return ctx_details


# ── Main ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Context removal audit sidecar — #49 Phase 2 Task 2.0"
    )
    parser.add_argument("--uri", default=None,
                        help="Override TORTOISE_DB_URI")
    parser.add_argument("--output", default=None,
                        help="Output path (default: data/migrations/2026-08-06_context_removal_audit.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print summary without writing file")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-context details")
    args = parser.parse_args()

    uri = args.uri or os.environ.get(
        "TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise"
    )
    cfg = _parse_uri(uri)

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(repo_root, "data", "migrations")
        output_path = os.path.join(output_dir, "2026-08-06_context_removal_audit.json")

    print("=" * 60)
    print("CONTEXT REMOVAL AUDIT SIDECAR")
    print("=" * 60)
    print(f"  URI:    {uri}")
    print(f"  Graph:  {cfg['graph']}")
    print(f"  Output: {output_path}")
    print()

    if args.dry_run:
        print("DRY RUN — will not write file.\n")

    # Use direct FalkorDB connection (avoids heavy index creation of FalkorProjection)
    from falkordb import FalkorDB as _FalkorDB
    db = _FalkorDB(
        host=cfg["host"], port=cfg["port"],
        password=cfg["password"] or None,
        socket_connect_timeout=5, socket_timeout=120,
    )
    g = db.select_graph(cfg["graph"])

    try:
        # ── Collect ─────────────────────────────────────────────────
        print("Collecting context→point mapping...")
        ctx_map = collect_context_map(g)
        ctx_details = collect_context_details(g)

        total_points = sum(len(ids) for ids in ctx_map.values())
        print(f"  Context values: {len(ctx_map)}")
        print(f"  Total points with context: {total_points}")
        print()

        # ── Build audit document ────────────────────────────────────
        timestamp = datetime.now(timezone.utc).isoformat()
        audit_doc = {
            "$schema": "tortoise/audit/context-removal-v1",
            "migration": "#49 Phase 2 — REMOVE context field",
            "generated_at": timestamp,
            "source_graph": cfg["graph"],
            "source_host": f"{cfg['host']}:{cfg['port']}",
            "summary": {
                "context_values": len(ctx_map),
                "total_points": total_points,
                "contexts_with_operators": sum(
                    1 for d in ctx_details.values() if d["connected_operators"] > 0
                ),
                "contexts_without_operators": sum(
                    1 for d in ctx_details.values() if d["connected_operators"] == 0
                ),
            },
            "context_details": ctx_details,
            "context_to_points": ctx_map,
            "restore_note": (
                "This is the ONLY permanent record of which context values existed "
                "and which points carried them. After Phase 2's REMOVE migration, "
                "n.context will no longer exist on any Point node. Use this sidecar "
                "to answer historical questions like 'what was in the licensing subgraph?'"
            ),
        }

        # ── Print summary ───────────────────────────────────────────
        print("Context value summary:")
        for ctx_val in sorted(ctx_map.keys()):
            detail = ctx_details.get(ctx_val, {})
            n_points = len(ctx_map[ctx_val])
            n_ops = detail.get("connected_operators", 0)
            kinds = detail.get("point_kinds", [])
            print(f"  {ctx_val:50s}  {n_points:4d} points  {n_ops:3d} connected ops  kinds={kinds}")

        # ── Write or dry-run ────────────────────────────────────────
        if args.dry_run:
            print(f"\nDRY RUN: Would write {len(json.dumps(audit_doc, indent=2))} bytes to {output_path}")
            print("\nFirst 3 context entries would be:")
            for i, (ctx_val, ids) in enumerate(list(ctx_map.items())[:3]):
                print(f"  {ctx_val}: {len(ids)} points")
        else:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(audit_doc, f, indent=2, ensure_ascii=False)
            print(f"\n✓ Written: {output_path}")
            print(f"  Size: {os.path.getsize(output_path)} bytes")

        print(f"\nAudit complete. {len(ctx_map)} contexts, {total_points} total points.")
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
