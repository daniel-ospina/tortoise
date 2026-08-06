#!/usr/bin/env python3
"""Migrate operator Points to have explicit `direction` property (ONTOLOGY v3.1 #189).

Sets `direction` on every existing operator Point, preserving current semantics:
  - NAND → "bidirectional" (always was symmetric)
  - IMPL with hasPart/partOf label → "bidirectional" (composition hierarchies)
  - All other IMPL (addresses, supports, no label) → "unidirectional"

Idempotent: skips operators that already have a `direction` property.

Usage:
  TORTOISE_DB_URI=docker://:@localhost:16379/tortoise python3 graph-scripts/migrate_direction.py
  # Or dry-run:
  TORTOISE_DB_URI=docker://:@localhost:16379/tortoise python3 graph-scripts/migrate_direction.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK


def main():
    parser = argparse.ArgumentParser(description="Migrate operator direction")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing")
    args = parser.parse_args()

    sdk = TortoiseSDK(db_path=None, namespace=None)
    proj = sdk._get_proj()

    # Find all operator Points
    rows = proj.g.query(
        "MATCH (o:Point) WHERE o.is_operator = true "
        "RETURN o.id, o.op_type, o.label, o.direction"
    ).result_set

    already_set = 0
    to_migrate: list[tuple[str, str]] = []  # [(op_id, new_direction), ...]

    for op_id, op_type, label, direction in rows:
        if direction is not None:
            already_set += 1
            continue

        # Determine direction per old semantics
        if op_type == "NAND":
            new_direction = "bidirectional"
        elif op_type == "IMPL" and label in ("hasPart", "partOf"):
            new_direction = "bidirectional"
        else:
            # Other IMPL: addresses, supports, or no label → unidirectional
            new_direction = "unidirectional"

        to_migrate.append((op_id, new_direction))

    total = len(rows)
    print(f"Total operators: {total}")
    print(f"Already have direction: {already_set}")
    print(f"To migrate: {len(to_migrate)}")
    print()

    n_bidi = sum(1 for _, d in to_migrate if d == "bidirectional")
    n_uni = sum(1 for _, d in to_migrate if d == "unidirectional")
    print(f"  → {n_bidi} bidirectional (NAND or hasPart/partOf IMPL)")
    print(f"  → {n_uni} unidirectional (other IMPL)")

    if args.dry_run:
        print("\n[Dry-run] No changes written.")
        sdk.close()
        return

    if not to_migrate:
        print("\nNothing to migrate.")
        sdk.close()
        return

    # Batch update: set direction property on each operator
    params = [{"id": op_id, "direction": d} for op_id, d in to_migrate]
    proj.g.query(
        "UNWIND $params AS p "
        "MATCH (o:Point {id: p.id}) "
        "SET o.direction = p.direction",
        params={"params": params},
    )

    print(f"\n✅ Migrated {len(to_migrate)} operators.")
    sdk.close()


if __name__ == "__main__":
    main()
