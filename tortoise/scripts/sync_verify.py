#!/usr/bin/env python3
"""#7048 Verification — count graph entities vs source data.

    python tortoise/scripts/sync_verify.py --db tortoise.db

Compares entity counts in the graph against expected source inputs:
  - Subjects by kind (team, role)
  - Objects by kind (repository, product, feature)
  - Points with aboutEntities set

ponytail: query + print; no framework needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root))

from tortoise.sdk import TortoiseSDK


def verify(db_path: str = "tortoise.db") -> dict:
    """Count entities by type and return summary."""
    sdk = TortoiseSDK(db_path)
    proj = sdk._get_proj()

    def _count(query: str, **params) -> int:
        rows = proj.g.query(query, params=params).result_set
        return rows[0][0] if rows else 0

    result = {
        "subjects": {
            "total": _count("MATCH (s:Subject) RETURN count(s)"),
            "teams": _count(
                "MATCH (s:Subject {subjectKind:'team'}) RETURN count(s)"
            ),
            "roles": _count(
                "MATCH (s:Subject {subjectKind:'role'}) RETURN count(s)"
            ),
        },
        "objects": {
            "total": _count("MATCH (o:Object) RETURN count(o)"),
            "repositories": _count(
                "MATCH (o:Object {objectKind:'repository'}) RETURN count(o)"
            ),
            "products": _count(
                "MATCH (o:Object {objectKind:'product'}) RETURN count(o)"
            ),
            "features": _count(
                "MATCH (o:Object {objectKind:'feature'}) RETURN count(o)"
            ),
        },
        "points_with_aboutEntities": _count(
            "MATCH (n:Point) WHERE n.aboutEntities IS NOT NULL "
            "AND n.aboutEntities <> '' RETURN count(n)"
        ),
        "total_points": _count(
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN count(n)"
        ),
    }
    sdk.close()
    return result


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="Verify entity counts in graph")
    ap.add_argument("--db", default="tortoise.db", help="Path to tortoise.db")
    ap.add_argument("--json", action="store_true", help="Output as JSON")
    args = ap.parse_args()
    result = verify(args.db)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Subjects: {result['subjects']['total']} "
              f"(teams={result['subjects']['teams']}, roles={result['subjects']['roles']})")
        print(f"Objects:  {result['objects']['total']} "
              f"(repos={result['objects']['repositories']}, "
              f"products={result['objects']['products']}, "
              f"features={result['objects']['features']})")
        print(f"Points:   {result['total_points']} "
              f"(with aboutEntities: {result['points_with_aboutEntities']})")
