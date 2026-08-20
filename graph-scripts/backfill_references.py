#!/usr/bin/env python3
"""Backfill Source → Entity references edges from existing Point→Source & about* edges.

Ontology v3.0 §3.2-3.3: (Point)-[:extractedFrom]->(Source)-[:references]->(Entity)

Strategy (no-migration decision):
  For each Source without references edges:
    find Points extracted from it: MATCH (p:Point)-[:extractedFrom]->(s:Source {url:$u})
    for each such Point, follow its about* edges: (p)-[about:aboutSubject|aboutObject]->(e)
    MERGE (s)-[:references]->(e)

Entity range = Document | Event | Object ONLY (Action dissolved in v3.0).

Idempotent — uses MERGE, safe to re-run.
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow running from worktree root or graph-scripts/ dir
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from tortoise.projection import FalkorProjection  # noqa: E402

# ── about* edges to follow (ONTOLOGY v2.5→v3.0 migration path) ──
# Point → Entity: aboutSubject → Subject, aboutObject → Object, aboutEvent → Event,
# aboutDocument → Document.  We do NOT follow aboutAction (Action dissolved v3.0).
_ABOUT_EDGES = {
    "aboutSubject": "Subject",
    "aboutObject": "Object",
    "aboutEvent": "Event",
    "aboutDocument": "Document",
}

# ── Cypher fragments ──────────────────────────────────────────────────

_SOURCES_WITHOUT_REFS = """
    MATCH (s:Source)
    OPTIONAL MATCH (s)-[r:references]->()
    WITH s, count(r) AS ref_count
    WHERE ref_count = 0
    RETURN s.url AS url, s.sourceKind AS sourceKind
    LIMIT $limit
"""

_POINTS_FROM_SOURCE = """
    MATCH (p:Point)-[:extractedFrom]->(s:Source {url:$url})
    RETURN p.id AS pid
"""

_ENTITY_FROM_POINT = """
    MATCH (p:Point {id:$pid})-[r:`{edge}`]->(e:{label})
    RETURN e.id AS eid, labels(e) AS labels
"""

_MERGE_REFERENCE = """
    MATCH (s:Source {url:$url}), (e:{label} {id:$eid})
    MERGE (s)-[:references]->(e)
"""


def backfill(db_uri: str, dry_run: bool = False, limit: int = 0) -> dict:
    """Run idempotent backfill. Returns stats dict."""
    proj = FalkorProjection.from_uri(db_uri)

    stats = {
        "sources_processed": 0,
        "references_created": 0,
        "sources_without_entity": 0,
    }

    limit_clause = limit if limit > 0 else 1000000
    params = {"limit": limit_clause}

    sources = proj.g.query(_SOURCES_WITHOUT_REFS, params=params).result_set
    print(f"Sources without references edges: {len(sources)}")

    for row in sources:
        source_url = row[0]
        source_kind = row[1]
        stats["sources_processed"] += 1
        found_entity = False

        # Find Points extracted from this Source
        points = proj.g.query(_POINTS_FROM_SOURCE, params={"url": source_url}).result_set
        print(f"  Source {source_url!r} ({source_kind}): {len(points)} extracted Point(s)")

        for (pid,) in points:
            for about_edge, label in _ABOUT_EDGES.items():
                cypher = _ENTITY_FROM_POINT.format(edge=about_edge, label=label)
                entities = proj.g.query(cypher, params={"pid": pid}).result_set
                for (eid, _labels) in entities:
                    found_entity = True
                    if dry_run:
                        stats["references_created"] += 1
                        print(f"    [DRY-RUN] Would create: (Source {source_url!r})-[:references]->({label} {eid!r})")
                    else:
                        proj.g.query(
                            _MERGE_REFERENCE.format(label=label),
                            params={"url": source_url, "eid": eid},
                        )
                        stats["references_created"] += 1
                        print(f"    MERGED (Source {source_url!r})-[:references]->({label} {eid!r})")

        if not found_entity:
            stats["sources_without_entity"] += 1
            print(f"    ⚠ No entities found (no about* edges from extracted Points)")  # noqa: F541

    proj.close()
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Backfill Source→Entity references edges (Ontology v3.0 §3.2-3.3)"
    )
    parser.add_argument(
        "--db-uri",
        default=os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise"),
        help="FalkorDB URI (default from TORTOISE_DB_URI or docker://:@localhost:16379/tortoise)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be created without making changes",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Limit number of Sources processed (0 = all)",
    )
    parser.add_argument(
        "--total-sources", action="store_true",
        help="Report total Source count (including those with existing references) and exit",
    )

    args = parser.parse_args()

    proj = FalkorProjection.from_uri(args.db_uri)

    if args.total_sources:
        total = proj.g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0]
        with_refs = proj.g.query(
            "MATCH (s:Source) OPTIONAL MATCH (s)-[r:references]->() "
            "WITH s, count(r) AS ref_count WHERE ref_count > 0 RETURN count(s)"
        ).result_set[0][0]
        without = proj.g.query(
            "MATCH (s:Source) OPTIONAL MATCH (s)-[r:references]->() "
            "WITH s, count(r) AS ref_count WHERE ref_count = 0 RETURN count(s)"
        ).result_set[0][0]
        print(f"Total Sources: {total}")
        print(f"  With references: {with_refs}")
        print(f"  Without references: {without}")
        proj.close()
        return

    stats = backfill(args.db_uri, dry_run=args.dry_run, limit=args.limit)

    print(f"\n{'='*60}")
    print(f"  BACKFILL {'DRY-RUN' if args.dry_run else 'COMPLETE'}")
    print(f"{'='*60}")
    print(f"  Sources processed:      {stats['sources_processed']}")
    print(f"  References {'would be ' if args.dry_run else ''}created: {stats['references_created']}")
    print(f"  Sources without entity:  {stats['sources_without_entity']}")


if __name__ == "__main__":
    main()
