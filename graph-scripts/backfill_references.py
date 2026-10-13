#!/usr/bin/env python3
"""Backfill Source → Entity references edges from existing Point→Source & about* edges.

Ontology v3.0 §3.2-3.3: (Point)-[:extractedFrom]->(Source)-[:references]->(Entity)

Strategy (no-migration decision):
  For each Source without references edges:
    find Points extracted from it: MATCH (p:Point)-[:extractedFrom]->(s:Source {url:$u})
    for each such Point, follow its about* edges: (p)-[about:aboutSubject|aboutObject]->(e)
    MERGE (s)-[:references]->(e)

Entity range = Source | Event | Object ONLY (Action dissolved in v3.0; the retired
`:Document` label is a `:Source` since D10 — ONTOLOGY v3.15 §4.4).

Idempotent — uses MERGE, safe to re-run.

⛔ THIS WRITER DOES NOT STAMP THE `sourceVersion` ANCHOR, and that is deliberate (#5213,
option 3). `_MERGE_REFERENCE` below is a bare `MERGE (s)-[:references]->(e)` — it is one
of the two non-provenance writers `docs/architecture/STORAGE-ARCHITECTURE.md` §9.6 names
as bypassing the pipeline guarantee (the other is the public `create_edge` escape hatch).
The `aboutEvent` / `aboutDocument` rows below mint DERIVATION edges, so what this script
writes reads `unknown` on every currency read — honest, never `current`.

⚠️ If you are here to ADD the anchor, note it is not a one-line change: every provenance
writer stamps `ON CREATE` only, so an edge this script has already written will NOT be
repaired by a later provenance link of the same pair — the later `MERGE` matches the
existing bare edge and the `ON CREATE` clause never fires. Also: the pre-D10 `"Document"`
signal the anchor discriminates on is a CALL-SITE relation alias, not a target label;
reaching it from here means passing `"Document"` as the relation spelling, not the
`Source` label this script resolves. Reopening the decision is on #5213 — update §9.6 and
`tests/test_references_boundary_5213.py` in the same change.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"graph-scripts/backfill_references.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python graph-scripts/backfill_references.py`"
    )

import argparse
import os

# Allow running from worktree root or graph-scripts/ dir
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from tortoise.projection import FalkorProjection, _writable_id  # noqa: E402

# ── about* edges to follow (ONTOLOGY v2.5→v3.0 migration path) ──
# Point → Entity: aboutSubject → Subject, aboutObject → Object, aboutEvent → Event,
# aboutDocument → Source.  We do NOT follow aboutAction (Action dissolved v3.0).
# D10 (ONTOLOGY v3.15 §4.4): a document is a :Source.
_ABOUT_EDGES = {
    "aboutSubject": "Subject",
    "aboutObject": "Object",
    "aboutEvent": "Event",
    "aboutDocument": "Source",
}

# ── the identity key per target LABEL ──
# The label and the key it is addressed by are ONE unit — the shared contract is
# `tortoise/projection/edges.py` STRUCTURAL_REL_LABELS / `stub_key` (ONTOLOGY
# §3.1). A retargeted label left on the old key resolves to NOTHING and still
# reports success, which is exactly how D10 half-landed here: the label moved to
# :Source while `RETURN e.id` / `MATCH (e {id:$eid})` stayed put, so a document
# Source minted without an `id` yielded NULL, matched no node, and was counted as
# linked anyway (#5141).
# :Source is the ONE label here that does NOT key on `id` — it keys on `url`
# (D10). Subject / Object / Event keep `id` deliberately: it is READ BACK from
# the very node the about edge points at, so it is an exact identity, whereas
# their `name` is a natural key two live nodes may share (#3590 D2) — a
# name-keyed MERGE would wire one reference into two carriers.
_ENTITY_KEY = {
    "Subject": "id",
    "Object": "id",
    "Event": "id",
    "Source": "url",
}

# ── Cypher fragments ──────────────────────────────────────────────────
#
# ⛔ NEVER run `str.format` (or an f-string) over these. They carry literal Cypher
# map literals — `{id:$pid}`, `{url:$url}` — and a template engine reads those
# braces as REPLACEMENT FIELDS: `.format` raised `KeyError: 'id'` on the first
# Point of the first Source, mid-run, after earlier Sources had already been
# written (#5141; it reproduces on origin/main's copy). `_fill` substitutes only
# the `@NAME@` markers below, which are not Cypher, so a fragment can never
# collide with its own substitution mechanism.

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
    MATCH (p:Point {id:$pid})-[r:`@EDGE@`]->(e:@LABEL@)
    RETURN e.@KEY@ AS ekey, labels(e) AS labels
"""

_MERGE_REFERENCE = """
    MATCH (s:Source {url:$url}), (e:@LABEL@ {@KEY@:$ekey})
    MERGE (s)-[:references]->(e)
"""


def _fill(fragment: str, **values: str) -> str:
    """Substitute this module's ``@NAME@`` markers — never ``str.format``.

    Fails loudly when a marker the caller named is absent, so a renamed fragment
    cannot ship a query still carrying the literal ``@LABEL@``.
    """
    query = fragment
    for name, value in values.items():
        marker = f"@{name.upper()}@"
        if marker not in query:
            raise ValueError(f"fragment carries no {marker} marker: {query!r}")
        query = query.replace(marker, value)
    return query


def backfill(db_uri: str, dry_run: bool = False, limit: int = 0) -> dict:
    """Run idempotent backfill against ``db_uri``. Returns stats dict.

    Owns the connection: opened here, closed on the way out.
    """
    proj = FalkorProjection.from_uri(db_uri)
    try:
        return backfill_graph(proj, dry_run=dry_run, limit=limit)
    finally:
        proj.close()


def backfill_graph(proj, dry_run: bool = False, limit: int = 0) -> dict:
    """The backfill loop itself, against an EXISTING projection.

    Split out of :func:`backfill` so a caller holding a handle already (a test)
    can drive the REAL loop and still read the graph afterwards —
    ``FalkorProjection.close`` tears an embedded server down. The caller keeps
    ownership: ``proj`` is never closed here.
    """
    stats = {
        "sources_processed": 0,
        "references_created": 0,
        "sources_without_entity": 0,
        "entities_unresolved": 0,
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
        # This Source was selected with ZERO `references` edges, so a repeat of
        # the same target is THIS run's own edge coming back. A MERGE would
        # MATCH it, and counting the match as a creation is the same over-report
        # (#5141): one edge per (label, key) target, counted once.
        linked: set[tuple[str, str]] = set()

        # Find Points extracted from this Source
        points = proj.g.query(_POINTS_FROM_SOURCE, params={"url": source_url}).result_set
        print(f"  Source {source_url!r} ({source_kind}): {len(points)} extracted Point(s)")

        for (pid,) in points:
            for about_edge, label in _ABOUT_EDGES.items():
                key = _ENTITY_KEY[label]
                entities = proj.g.query(
                    _fill(_ENTITY_FROM_POINT, edge=about_edge, label=label, key=key),
                    params={"pid": pid},
                ).result_set
                for (ekey, _labels) in entities:
                    if not _writable_id(ekey):
                        # The key IS the MERGE key, so an entity without a usable
                        # one is unaddressable — and skipping it silently while
                        # still counting it is the fail-open this script was
                        # reported for. Reuse the writers' own identity gate
                        # (#7369) instead of a second definition of it.
                        stats["entities_unresolved"] += 1
                        print(
                            f"    ⚠ {label} target of {about_edge} from Point "
                            f"{pid!r} carries no usable {key} — skipped"
                        )
                        continue
                    if (label, ekey) in linked:
                        continue
                    linked.add((label, ekey))
                    found_entity = True
                    if dry_run:
                        stats["references_created"] += 1
                        print(
                            f"    [DRY-RUN] Would create: (Source {source_url!r})"
                            f"-[:references]->({label} {ekey!r})"
                        )
                        continue
                    proj.g.query(
                        _fill(_MERGE_REFERENCE, label=label, key=key),
                        params={"url": source_url, "ekey": ekey},
                    )
                    stats["references_created"] += 1
                    print(
                        f"    MERGED (Source {source_url!r})-[:references]->"
                        f"({label} {ekey!r})"
                    )

        if not found_entity:
            stats["sources_without_entity"] += 1
            print("    ⚠ No linkable entities found (no about* edges from "
                  "extracted Points, or their targets carry no identity key)")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Backfill Source→Entity references edges (Ontology v3.0 §3.2-3.3)"
    )
    parser.add_argument(
        "--db-uri",
        default=os.environ.get("TORTOISE_DB_URI", "docker://:@127.0.0.1:16379/tortoise"),
        help="FalkorDB URI (default from TORTOISE_DB_URI or docker://:@127.0.0.1:16379/tortoise)",
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

    if args.total_sources:
        # Own the handle for the report only. The pre-existing unconditional
        # open leaked this one whenever the report was NOT requested (the
        # `backfill` call below opens its own), and in embedded mode a
        # never-closed handle holds the server up for the process's life.
        proj = FalkorProjection.from_uri(args.db_uri)
        try:
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
        finally:
            proj.close()
        return

    stats = backfill(args.db_uri, dry_run=args.dry_run, limit=args.limit)

    print(f"\n{'='*60}")
    print(f"  BACKFILL {'DRY-RUN' if args.dry_run else 'COMPLETE'}")
    print(f"{'='*60}")
    print(f"  Sources processed:      {stats['sources_processed']}")
    print(f"  References {'would be ' if args.dry_run else ''}created: {stats['references_created']}")
    print(f"  Sources without entity:  {stats['sources_without_entity']}")
    print(f"  Unlinkable targets:      {stats['entities_unresolved']}")


if __name__ == "__main__":
    main()
