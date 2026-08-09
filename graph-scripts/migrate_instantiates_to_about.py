#!/usr/bin/env python3
"""One-off migration (#281): convert Event-[:INSTANTIATES]->Object to
Event-[:aboutObject]->Object per ONTOLOGY v3.2 (predicate removed in #214).

Usage:
  python3 graph-scripts/migrate_instantiates_to_about.py --dry-run [--db URI] [--graphs a,b]
  python3 graph-scripts/migrate_instantiates_to_about.py [--db URI] [--graphs a,b]

Runs against EVERY graph namespace that could carry the edge: the URI-default
graph (derived exactly as sdk.py does — ``urlparse(uri).path.lstrip('/') or
"tortoise"``), plus ``*_tortoise`` (registry/selfhost) and ``team_*`` (hosted
tenant) graphs from ``list_graphs()``. ``--graphs`` overrides the filter with
an explicit list. Dry-run selects and counts only; live mode converts per
graph with per-graph try/except so one graph's failure leaves others intact.
Re-running is safe: after a live run every graph reports zero remaining
INSTANTIATES edges.
"""
from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tortoise.projection import FalkorProjection  # noqa: E402

MIGRATE = (
    "MATCH (e:Event)-[r:INSTANTIATES]->(o:Object) "
    "MERGE (e)-[:aboutObject]->(o) DETACH DELETE r"
)
COUNT = "MATCH ()-[:INSTANTIATES]->() RETURN count(*)"


def derive_uri_graph(uri: str) -> str:
    """The default graph a URI addresses — identical derivation to sdk.py."""
    return urlparse(uri).path.lstrip("/") or "tortoise"


def target_graphs(uri: str, explicit: list[str] | None) -> tuple[list[str], list[str]]:
    """Return (targets, excluded) graph lists."""
    if explicit:
        return explicit, []
    uri_graph = derive_uri_graph(uri)
    proj = FalkorProjection.from_uri(uri, graph_name=uri_graph)
    try:
        all_graphs = proj.list_graphs()
    finally:
        proj.close()
    targets = []
    excluded = []
    for g in all_graphs:
        if g == uri_graph or g.endswith("_tortoise") or g.startswith("team_"):
            targets.append(g)
        else:
            excluded.append(g)
    # The URI graph may not be listed (FalkorDB's default graph often isn't);
    # it is still a target — the query addresses it via the projection.
    if uri_graph not in targets:
        targets.insert(0, uri_graph)
    return targets, excluded


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="TORTOISE_DB_URI override (default: env)")
    ap.add_argument("--graphs", default=None, help="comma-separated explicit graph list")
    ap.add_argument("--dry-run", action="store_true", help="select + count only, no writes")
    args = ap.parse_args()

    uri = args.db or os.environ.get("TORTOISE_DB_URI", "")
    if not uri:
        print("ERROR: no DB URI — pass --db or set TORTOISE_DB_URI", file=sys.stderr)
        return 1

    explicit = [g.strip() for g in args.graphs.split(",")] if args.graphs else None
    targets, excluded = target_graphs(uri, explicit)

    print(f"DB URI graph: {derive_uri_graph(uri)}")
    print(f"Target graphs ({len(targets)}): {', '.join(targets) or '(none)'}")
    if excluded:
        print(f"EXCLUDED graphs ({len(excluded)}): {', '.join(excluded)}")

    total = 0
    for gname in targets:
        try:
            proj = FalkorProjection.from_uri(uri, graph_name=gname)
            try:
                rows = proj.g.query(COUNT).result_set
                before = rows[0][0] if rows else 0
                total += before
                if args.dry_run:
                    print(f"[dry-run] {gname}: {before} INSTANTIATES edge(s) — no write")
                    continue
                if before:
                    proj.g.query(MIGRATE)
                after_rows = proj.g.query(COUNT).result_set
                after = after_rows[0][0] if after_rows else 0
                print(f"[live]    {gname}: {before} → {after} INSTANTIATES edge(s)")
            finally:
                proj.close()
        except Exception as exc:  # noqa: BLE001 — per-graph isolation
            print(f"[ERROR]   {gname}: {exc!r} (remaining graphs untouched)", file=sys.stderr)

    if args.dry_run:
        print(f"\nTotal INSTANTIATES edges to migrate: {total}")
    else:
        print("\nMigration complete. Re-run --dry-run to confirm zero remaining edges.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
