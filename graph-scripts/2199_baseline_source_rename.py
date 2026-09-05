#!/usr/bin/env python3
"""One-time upgrade migration (#2199): rename the baseline_source token family.

Pre-#2199 graphs stamp point baselines with the provenance tokens
``baseline_source='explicit'`` and ``baseline_source='inherited'``. Issue
#2199 (owner-confirmed, 2026-09-03) renames the family so the tokens are
self-explanatory:

    'explicit'  → 'set-by-author'          (belief stated by whoever filed it)
    'inherited' → 'inherited-from-source'  (belief from the source's tier at EP
                                            run)
    (new) 'system-default'                 (no belief stated → the system's
                                            standard starting belief; no
                                            deployed data — nothing to migrate)

The engine (tortoise/sdk.py set_point_baseline) now REJECTS the old
spellings with this script's name in the error, so code equality sites and
deployed data never coexist across the two vocabularies. **Run this once per
graph before deploying the post-#2199 engine.** ``system-default`` is new
(no deployed data) — nothing to do for it.

Usage:
  python3 graph-scripts/2199_baseline_source_rename.py --dry-run [--db URI] [--graphs a,b]
  python3 graph-scripts/2199_baseline_source_rename.py [--db URI] [--graphs a,b]

Runs against every graph namespace that could carry Points: the URI-default
graph (derived exactly as sdk.py does — ``urlparse(uri).path.lstrip('/') or
"tortoise"``), plus ``*_tortoise`` (registry/selfhost) and ``team_*`` (hosted
tenant) graphs from ``list_graphs()``. ``--graphs`` overrides the filter with
an explicit list. Dry-run selects and counts only; live mode renames per
graph with per-graph try/except so one graph's failure leaves others intact.
Re-running is safe: after a live run every graph reports zero remaining
legacy spellings.

The module-level functions (``count_legacy`` / ``rename_legacy``) take any
projection object exposing ``.g.query`` — they are importable so tests can
exercise the migration against an isolated graph.
"""
from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tortoise.projection import FalkorProjection  # noqa: E402, RUF100

# Old → new spellings (issue #2199 direction). New-token constants live in
# tortoise/sdk.py; import them so this map can never drift from the engine's
# equality sites.
from tortoise.sdk import (
    BASELINE_SOURCE_INHERITED,
    BASELINE_SOURCE_SET_BY_AUTHOR,
)

RENAME: dict[str, str] = {
    "explicit": BASELINE_SOURCE_SET_BY_AUTHOR,
    "inherited": BASELINE_SOURCE_INHERITED,
}

LEGACY_TOKENS = tuple(RENAME)
COUNT = (
    "MATCH (n:Point) WHERE n.baseline_source IN $tokens RETURN count(*)"
)
RENAME_ONE = (
    "MATCH (n:Point) WHERE n.baseline_source = $old SET n.baseline_source = $new"
)


def derive_uri_graph(uri: str) -> str:
    """The default graph a URI addresses — identical derivation to sdk.py."""
    return urlparse(uri).path.lstrip("/") or "tortoise"


def count_legacy(proj) -> int:
    """Count :Point rows still carrying a pre-#2199 baseline_source spelling."""
    rows = proj.g.query(COUNT, params={"tokens": list(LEGACY_TOKENS)}).result_set
    return rows[0][0] if rows else 0


def rename_legacy(proj) -> dict[str, int]:
    """Rename legacy baseline_source tokens in place; return per-spelling counts."""
    moved: dict[str, int] = {}
    for old, new in RENAME.items():
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.baseline_source = $old RETURN count(*)",
            params={"old": old},
        ).result_set
        n = rows[0][0] if rows else 0
        if n:
            proj.g.query(RENAME_ONE, params={"old": old, "new": new})
        moved[old] = n
    return moved


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
                before = count_legacy(proj)
                total += before
                if args.dry_run:
                    print(f"[dry-run] {gname}: {before} legacy baseline_source row(s) — no write")
                    continue
                moved = rename_legacy(proj) if before else {}
                after = count_legacy(proj)
                detail = ", ".join(f"{k}→{v}: {n}" for k, v in RENAME.items()
                                   for n in [moved.get(k, 0)])
                print(f"[live]    {gname}: {before} → {after} legacy row(s) "
                      f"({detail})")
            finally:
                proj.close()
        except Exception as exc:  # noqa: BLE001, RUF100
            print(f"[ERROR]   {gname}: {exc!r} (remaining graphs untouched)", file=sys.stderr)

    if args.dry_run:
        print(f"\nTotal legacy baseline_source rows to rename: {total}")
    else:
        print("\nMigration complete. Re-run --dry-run to confirm zero legacy rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
