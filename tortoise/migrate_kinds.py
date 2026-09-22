#!/usr/bin/env python3
"""Migration: update kind values for kinds moved from core to packs.

Run once after packs are loaded. Idempotent — safe to run multiple times.
Handles all entity types: Point, Object, Subject, Event, Document, Source.

Usage:
    python3 tortoise/migrate_kinds.py
    python3 tortoise/migrate_kinds.py --dry-run
"""
from __future__ import annotations

import sys

from tortoise.sdk import TortoiseSDK

# Entity type → (Neptune label, kind property name)
ENTITY_LABELS: dict[str, tuple[str, str]] = {
    "object":   ("Object",   "objectKind"),
    "point":    ("Point",    "pointKind"),
    "subject":  ("Subject",  "subjectKind"),
    "event":    ("Event",    "eventKind"),
    "document": ("Document", "documentKind"),
    "source":   ("Source",   "sourceKind"),
}

# Migration entries: old core kind → (new pack-prefixed kind, entity_type)
MIGRATIONS: list[tuple[str, str, str]] = [
    # ── Object kinds ───────────────────────────────────────────────────
    ("product",      "product-strategy:product",      "object"),
    ("customer",     "product-strategy:customer",     "object"),
    ("competitor",   "product-strategy:competitor",   "object"),
    ("customerSegment", "product-strategy:customerSegment", "object"),
    ("market",       "product-strategy:market",       "object"),
    ("epic",         "dev:epic",                      "object"),
    ("issue",        "dev:issue",                     "object"),
    ("code",         "dev:code",                      "object"),
    ("api",          "dev:api",                       "object"),
    ("database",     "dev:database",                  "object"),
    ("software",     "dev:software",                  "object"),
    ("infrastructure", "dev:infrastructure",          "object"),
    ("deployment",   "dev:deployment",                "object"),
    ("indicator",    "dev:indicator",                 "object"),

    # ── Point kinds ────────────────────────────────────────────────────
    ("useCase",      "product-strategy:useCase",      "point"),
    ("jobToBeDone",  "product-strategy:jobToBeDone",  "point"),
    ("userJourney",  "product-strategy:userJourney",  "point"),
    ("workflow",     "product-strategy:workflow",     "point"),
    ("valueProposition", "product-strategy:valueProposition", "point"),
    ("requirement",  "dev:requirement",               "point"),
    # bug is now an OBJECT kind (Problem subclass, dev 0.3.0 problem-family
    # landing) — a legacy bug POINT can no longer be re-typed dev:bug point
    # (commit-schema calibration). Legacy dev:bug points pre-date the re-
    # model; follow-up decides point→object relocation (risk is the point
    # form).
    ("technicalDebt", "dev:technicalDebt",            "point"),

    # ── Document kinds ─────────────────────────────────────────────────
    ("competitiveAnalysis", "product-strategy:competitiveAnalysis", "document"),
    ("marketResearch",      "product-strategy:marketResearch",      "document"),
    ("productSpec",         "product-strategy:productSpec",         "document"),
    ("featureSpec",         "product-strategy:featureSpec",         "document"),
    ("architectureDoc",     "dev:architectureDoc",                  "document"),
    ("apiSpec",             "dev:apiSpec",                          "document"),
    ("deployRunbook",       "dev:deployRunbook",                    "document"),

    # ── Event kinds ────────────────────────────────────────────────────
    ("cardCreated",     "pm:cardCreated",     "event"),
    ("cardMoved",       "pm:cardMoved",       "event"),
    ("sprintStarted",   "pm:sprintStarted",   "event"),
    ("sprintCompleted", "pm:sprintCompleted", "event"),
    ("stepStarted",     "pm:stepStarted",     "event"),
    ("stepCompleted",   "pm:stepCompleted",   "event"),
    ("gatePassed",      "pm:gatePassed",      "event"),
    ("gateBlocked",     "pm:gateBlocked",     "event"),
]


def _do_migrate(sdk: TortoiseSDK, dry_run: bool = False) -> dict:
    """Run kind migration across all entity types.

    Returns {old_kind: {entity_type: str, new: str, count: int}}.
    """
    proj = sdk._get_proj()
    results: dict[str, dict] = {}

    for old_kind, new_kind, entity_type in MIGRATIONS:
        label, kind_prop = ENTITY_LABELS[entity_type]
        if dry_run:
            rows = proj.g.query(
                f"MATCH (n:{label} {{{kind_prop}: $old}}) "
                f"RETURN count(n)",
                params={"old": old_kind},
            ).result_set
        else:
            rows = proj.g.query(
                f"MATCH (n:{label} {{{kind_prop}: $old}}) "
                f"SET n.{kind_prop} = $new "
                f"RETURN count(n)",
                params={"old": old_kind, "new": new_kind},
            ).result_set

        count = rows[0][0] if rows else 0
        if count > 0 or dry_run:
            results[old_kind] = {
                "entity_type": entity_type,
                "new": new_kind,
                "count": count,
            }
            action = "would update" if dry_run else "updated"
            print(f"  {entity_type}:{old_kind} → {new_kind}: {count} nodes {action}")

    return results


def migrate(sdk: TortoiseSDK, dry_run: bool = False) -> dict:
    """Public API: run kind migration across all entity types.

    Thin wrapper over `_do_migrate` (kept as a public entry point for
    programmatic consumers; the leading-underscore rename in #442 broke
    `from tortoise.migrate_kinds import migrate`).
    """
    return _do_migrate(sdk, dry_run=dry_run)


if __name__ == "__main__":
    import os

    dry_run = "--dry-run" in sys.argv

    from tortoise.config import resolve_db_path, is_docker_uri  # noqa: I001
    uri = os.environ.get("TORTOISE_DB_URI")
    if not uri:
        # Embedded migration against canonical path (issue #176)
        print(f"Using embedded DB at {resolve_db_path()} (set TORTOISE_DB_URI to run against a server)")
        sdk = TortoiseSDK(db_path=resolve_db_path())
        results = _do_migrate(sdk, dry_run=dry_run)
        exit(0)
    if not is_docker_uri(uri):
        print("Set TORTOISE_DB_URI to a docker:// URI to run migration")
        exit(1)

    sdk = TortoiseSDK(uri)
    results = _do_migrate(sdk, dry_run=dry_run)

    total = sum(r["count"] for r in results.values())
    by_type: dict[str, int] = {}
    for r in results.values():
        by_type[r["entity_type"]] = by_type.get(r["entity_type"], 0) + r["count"]

    action = "would be migrated" if dry_run else "migrated"
    print(f"\nTotal: {total} nodes {action} across {len(results)} kinds")
    for etype, cnt in sorted(by_type.items()):
        print(f"  {etype}: {cnt}")
