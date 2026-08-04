#!/usr/bin/env python3
"""Migration: update pointKind values for kinds moved from core to packs.

Run once after packs are loaded. Idempotent — safe to run multiple times.

Usage:
    python3 tortoise/scripts/migrate_kinds.py
"""
from tortoise.sdk import TortoiseSDK
from tortoise.pack_registry import PackRegistry

# Old core kind → new pack-prefixed kind
MIGRATIONS = {
    "product": "product-strategy:product",
    "customer": "product-strategy:customer",
    "competitor": "product-strategy:competitor",
    "epic": "dev:epic",
    "code": "dev:code",
    "api": "dev:api",
    "database": "dev:database",
    "software": "dev:software",
    "infrastructure": "dev:infrastructure",
    "indicator": "dev:indicator",
    "useCase": "product-strategy:useCase",
    "jobToBeDone": "product-strategy:jobToBeDone",
    "userJourney": "product-strategy:userJourney",
    "workflow": "product-strategy:workflow",
    "requirement": "dev:requirement",
    "issue": "dev:issue",
}

def migrate(sdk: TortoiseSDK) -> dict:
    """Run kind migration. Returns {old_kind: count_updated}."""
    proj = sdk._get_proj()
    results = {}
    for old, new in MIGRATIONS.items():
        rows = proj.g.query(
            "MATCH (n:Point {pointKind: $old}) "
            "SET n.pointKind = $new "
            "RETURN count(n)",
            params={"old": old, "new": new},
        ).result_set
        count = rows[0][0] if rows else 0
        if count > 0:
            results[old] = count
            print(f"  {old} → {new}: {count} points updated")
    return results

if __name__ == "__main__":
    import os
    uri = os.environ.get("TORTOISE_DB_URI")
    if not uri:
        print("Set TORTOISE_DB_URI to run migration")
        exit(1)
    sdk = TortoiseSDK(uri)
    results = migrate(sdk)
    total = sum(results.values())
    print(f"\nTotal: {total} points migrated across {len(results)} kinds")
