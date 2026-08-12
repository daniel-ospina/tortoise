#!/usr/bin/env python3
"""Backfill is_operator = false on Points missing the property (#522).

Semantic decision (issue #522): NULL means non-operator. After this
backfill, every Point has an explicit boolean, so queries can use the
indexable `n.is_operator = false` form instead of the previous unindexable
`(n.is_operator IS NULL OR n.is_operator = false)` disjunction (full
Node By Label Scan on every Point).

Also repairs legacy stale composite Point indexes: an older FalkorDB build
created a composite `:Point(id, pointKind, content_hash, content,
is_operator)` index whose boolean-false lookups silently miss (verified on
a 19k-point DB — `= false` returned 0 while `<> true` returned the full
non-operator set). The composite is dropped and the canonical per-property
indexes (id/pointKind/content_hash/is_operator) are recreated, matching
projection `_ensure_indexes`.

Requires #327's Point.is_operator RANGE index (already on main).

Idempotent: only touches Points where is_operator IS NULL; index drop is
best-effort. Safe to re-run.

Usage:
    TORTOISE_DB_URI=docker://:@localhost:16379/tortoise \
        python3 graph-scripts/backfill_is_operator.py [--dry-run] [--yes]

Test safety: always verify graph name before running. For tests, use
test-prefixed graphs (tortoise_test_*) and pass --yes to skip confirmation.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK


# Legacy composite index (older FalkorDB build) whose boolean-false
# lookups are broken — dropped during backfill so `= false` queries
# return correct results (#522).
_LEGACY_COMPOSITE_INDEX = (
    "DROP INDEX ON :Point(id, pointKind, content_hash, content, is_operator)"
)


# Canonical per-property Point indexes — mirror projection._ensure_indexes.
_CANONICAL_POINT_PROPS = ("id", "pointKind", "content_hash", "is_operator")


def test_guard(graph_name: str, yes: bool = False) -> None:
    """Safety gate: confirm before running on non-test graphs."""
    if graph_name.startswith("tortoise_test_") or graph_name.startswith("test_"):
        print(f"✅ Test graph detected ({graph_name}) — proceeding")
        return
    if yes:
        print(f"⚠️  Production graph ({graph_name}) — --yes flag set, proceeding")
        return
    print(f"\n⚠️  Target graph is '{graph_name}' — NOT a test graph.")
    print("    This script stamps is_operator=false on ALL Points missing the property.")
    print("    Run with --yes to confirm, or use a test-prefixed graph.")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Backfill is_operator=false on Points (#522)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing")
    parser.add_argument("--yes", action="store_true",
                        help="Skip confirmation on non-test graphs")
    args = parser.parse_args()

    sdk = TortoiseSDK(db_path=None, namespace=None)
    proj = sdk._get_proj()

    graph_name = getattr(proj, "graph_name", None) or "tortoise"
    test_guard(str(graph_name), args.yes)

    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator IS NULL "
        "RETURN count(n)"
    ).result_set
    total = rows[0][0] if rows else 0
    print(f"Points missing is_operator: {total}")

    if args.dry_run:
        print("\n[Dry-run] No changes written.")
        sdk.close()
        return

    if total > 0:
        proj.g.query(
            "MATCH (n:Point) WHERE n.is_operator IS NULL "
            "SET n.is_operator = false"
        )
        print(f"Backfilled {total} Points.")

    # Repair legacy composite index (best-effort — may not exist on
    # fresh DBs or on FalkorDB builds without composite-index support).
    try:
        proj.g.query(_LEGACY_COMPOSITE_INDEX)
        print("Dropped legacy composite Point index (stale boolean-false lookups).")
    except Exception as e:
        msg = str(e).lower()
        if "no such index" in msg or "already" in msg or "does not exist" in msg:
            print("No legacy composite index to drop.")
        else:
            print(f"⚠️  Could not drop legacy composite index: {e}")

    # Recreate canonical per-property indexes (idempotent — errors on
    # already-indexed are expected and ignored).
    for prop in _CANONICAL_POINT_PROPS:
        try:
            proj.g.query(f"CREATE INDEX FOR (n:Point) ON (n.{prop})")
        except Exception:
            pass  # already indexed — expected

    # Verify
    after = proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator IS NULL RETURN count(n)"
    ).result_set
    remaining = after[0][0] if after else 0
    print(f"Remaining NULL: {remaining}")

    if remaining > 0:
        print("⚠️  Some Points still missing is_operator — investigate.")
        sys.exit(1)

    sdk.close()


if __name__ == "__main__":
    main()
