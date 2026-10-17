#!/usr/bin/env python3
"""One-time migration (#4125): pre-#4005 basename-url session Sources.

#4005 replaced the hosted commit path's session-Source identity (the raw's
BASENAME — ``'session.md'``) with the canonical ``session:<session_id>``
ONTOLOGY §4.6 registers. Nodes minted before that fix were never rewritten, so
a session that re-commits now holds TWO ``agentSession`` ``:Source`` nodes (the
legacy basename one plus ``session:<id>``) and the legacy one is unreachable by
``delete_session`` (it leaks on delete).

This script runs the migration over every graph that could hold one, using the
SAME reconciliation the replay engines run at the end of a rebuild
(``tortoise.session_source_migration.reconcile_legacy_session_sources``) — one
implementation, so the operator path and the durability path cannot drift.

Attribution is derived, never invented. A legacy node is migrated onto
``session:<id>`` only when a session can be derived for it: from a Point's own
``session_id`` on its ``extractedFrom`` edge (journal-durable) or from a
Document's ``sessionId`` on its ``references`` edge (live-only). A node no
session can be attributed to is REPORTED and left in place — inventing an
identity would be worse than a stranded node.

Usage:
  uv run python graph-scripts/4125_migrate_legacy_session_sources.py --dry-run
  uv run python graph-scripts/4125_migrate_legacy_session_sources.py
  uv run python graph-scripts/4125_migrate_legacy_session_sources.py \
      --db bolt://... --graphs org_acme_tortoise --json

``--dry-run`` selects and reports only — no write of any kind. Live mode is
idempotent (a re-run finds nothing to do), so it is safe to repeat. Runs against
the URI-default graph (derived exactly as sdk.py does), plus every ``*_tortoise``
(registry/selfhost) and ``org_*``/``team_*`` (hosted tenant) graph from
``list_graphs()``; ``--graphs`` overrides the filter. One graph's failure leaves
the others untouched.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"graph-scripts/4125_migrate_legacy_session_sources.py requires Python "
        f">= 3.12 (got {sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python graph-scripts/4125_migrate_legacy_session_sources.py`"
    )

import argparse
import json
import os
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tortoise.projection import FalkorProjection  # noqa: E402, RUF100
from tortoise.session_source_migration import (
    find_legacy_session_sources,
    reconcile_legacy_session_sources,
)


def derive_uri_graph(uri: str) -> str:
    """The default graph a URI addresses — identical derivation to sdk.py.

    An EMBEDDED path (no scheme) is not a URI: it names one store, and its
    ``graph_name`` is None (the embedded backend has a single graph).
    """
    if "://" not in uri:
        return ""
    return urlparse(uri).path.lstrip("/") or "tortoise"


def open_projection(uri: str, graph_name: str):
    """Open the projection for *uri* — a server URI, or an embedded DB path.

    ``from_uri`` refuses a scheme-less value (``docker://``/``redis://``/
    ``rediss://`` only), so an embedded deployment — which is exactly where a
    pre-#4005 node can also live — is opened directly. One graph, nothing to
    enumerate.
    """
    if "://" not in uri:
        return FalkorProjection(uri, allow_nonstandard_path=True)
    return FalkorProjection.from_uri(uri, graph_name=graph_name)


def target_graphs(uri: str, explicit: list[str] | None) -> tuple[list[str], list[str]]:
    """Return (targets, excluded) graph lists."""
    if explicit:
        return explicit, []
    uri_graph = derive_uri_graph(uri)
    if not uri_graph:
        # Embedded: one store, one graph — nothing to enumerate or exclude.
        return [""], []
    proj = open_projection(uri, uri_graph)
    try:
        all_graphs = proj.list_graphs()
    finally:
        proj.close()
    targets, excluded = [], []
    for g in all_graphs:
        # #3543: tenant graphs are `org_*` post-rename and `team_*` before it
        # (no data migration rewrites the namespace) — target both.
        if (g == uri_graph or g.endswith("_tortoise")
                or g.startswith(("org_", "team_"))):
            targets.append(g)
        else:
            excluded.append(g)
    if uri_graph not in targets:
        targets.insert(0, uri_graph)
    return targets, excluded


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="TORTOISE_DB_URI override (default: env)")
    ap.add_argument("--graphs", default=None, help="comma-separated explicit graph list")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the candidates + plan only; NO writes")
    ap.add_argument("--json", action="store_true",
                    help="emit the machine-readable report on stdout")
    args = ap.parse_args()

    uri = args.db or os.environ.get("TORTOISE_DB_URI", "")
    if not uri:
        print("ERROR: no DB URI — pass --db or set TORTOISE_DB_URI", file=sys.stderr)
        return 1

    explicit = [g.strip() for g in args.graphs.split(",")] if args.graphs else None
    targets, excluded = target_graphs(uri, explicit)

    reports: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for gname in targets:
        try:
            proj = open_projection(uri, gname)
            try:
                if args.dry_run:
                    # The read-only audit: the SAME plan the live run would
                    # execute — `reconcile_legacy_session_sources(dry_run=True)`
                    # writes nothing (`_merge_metadata` is skipped and the move
                    # primitives short-circuit to a count) and returns the SAME
                    # report shape, so `migrated` / `edges_repointed` /
                    # `ambiguous` / `blocked` mean one thing in both modes
                    # (#8229 review, P2: the hand-built plan below hardcoded
                    # those to empty, so a blocked migration pre-flighted as
                    # clean). `find_legacy_session_sources` stays as the
                    # per-node audit detail (it is the only shape carrying the
                    # attribution that justifies a move).
                    found = find_legacy_session_sources(proj.g)
                    report = reconcile_legacy_session_sources(
                        proj.g, dry_run=True)
                    report["candidate_detail"] = found
                else:
                    report = reconcile_legacy_session_sources(proj.g)
            finally:
                proj.close()
        except Exception as exc:  # noqa: BLE001, RUF100
            errors[gname] = repr(exc)
            reports[gname] = {"error": repr(exc)}
            continue
        reports[gname] = report
        label = gname or os.path.basename(uri)
        if not args.json:
            verb = "[dry-run]" if args.dry_run else "[live]   "
            print(f"{verb} {label}: {len(report['candidates'])} legacy node(s) "
                  f"→ {len(report['migrated'])} migrated, "
                  f"{report['edges_repointed']} edge(s) re-pointed, "
                  f"{len(report['canonical_created'])} canonical node(s) minted; "
                  f"{len(report['unattributed'])} unattributed, "
                  f"{len(report['ambiguous'])} ambiguous, "
                  f"{len(report['blocked'])} blocked")
            for c in report.get("candidate_detail", []):
                print(f"          {c['url']}: sessions={c['sessions'] or '(none)'} "
                      f"degree={c['degree']}")
            for url in report["unattributed"]:
                print(f"          ⚠️ {url}: no session derivable — LEFT IN PLACE")
            for url, rels in (report.get("blocked") or {}).items():
                note = (report.get("notes") or {}).get(url)
                print(f"          ⚠️ {url}: unmovable relation(s) {rels} — "
                      "LEFT IN PLACE" + (f" ({note})" if note else ""))
            other = report.get("other_agent_session") or []
            if other:
                # Not candidates: another producer's LIVE, keyed identity (the
                # indexer's `corpus://` agentSession Source). Reported so the
                # audit is not silent about a population it leaves alone.
                print(f"          ℹ️ {len(other)} keyed agentSession Source(s) "
                      f"left alone (not the pre-#4005 basename identity): "
                      f"{', '.join(other)}")

    if args.json:
        print(json.dumps(reports, indent=2, default=str))

    if not args.json:
        if excluded:
            print(f"EXCLUDED graphs ({len(excluded)}): {', '.join(excluded)}")
        if errors:
            print(f"ERRORED graphs ({len(errors)}): {', '.join(errors)}",
                  file=sys.stderr)
        if args.dry_run:
            print("\nDry run — nothing written. Re-run without --dry-run to migrate.")
        else:
            print("\nMigration complete. Re-run --dry-run to confirm 0 candidates.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
