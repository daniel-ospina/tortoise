#!/usr/bin/env python3
"""#1714 / #1725: historical `observation`-duplicate dedup (deliver-or-defer).

CONTEXT (recorded decision, amend 7/16 — owner: Slice-0 implementer #1725):
Prior live runs of the pre-fix indexer minted UNKEYED `observation` Points
(kind="observation", props {source, github_url, github_repo, github_state,
github_number} — no externalId). The fixed indexer writes KEYED `statement`
Points (externalId `github:issue:{repo}#{n}`) and NEVER emits `observation`
(removed kind, ONTOLOGY §5). Teams that ran the old indexer hold duplicate
truth: an unkeyed observation + a keyed statement for the same issue.

DECISION: DEFAULT LEAVE-AS-IS for live graphs (the duplicates are harmless —
the statement is current-truth; observation nodes are legacy). This script is
an OPT-IN best-effort merge for teams that want dedup: each duplicate
observation is SUPERSEDED into its keyed statement twin (supersede_point —
CORRECTS + edge transfer + bi-temporal), so the graph converges on one
current statement per issue.

The merge is keyed by github_url (the only stable link between the old
observation and the new statement). Observations with NO statement twin are
reported but left untouched (best-effort).

Usage:
    python3 graph-scripts/1714_dedup_observation.py [--dry-run] [--merge]
        [--uri URI] [--graph GRAPH] [--yes]

    --dry-run  report only (DEFAULT — no writes)
    --merge    perform the supersede merges (opt-in)
    Defaults to TORTOISE_DB_URI env var (or docker://:falkordb@localhost:16379/tortoise).
    Hosted multi-tenant: run once per tenant graph (--graph team_<team_id>).
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

DEFAULT_URI = "docker://:falkordb@localhost:16379/tortoise"


def _resolve_uri(args_uri: str) -> str:
    """--uri > TORTOISE_DB_URI > embedded default path."""
    uri = args_uri or os.environ.get("TORTOISE_DB_URI", "") or DEFAULT_URI
    if uri == DEFAULT_URI:
        from tortoise.config import resolve_db_path
        return resolve_db_path()
    return uri


def _connect(args):
    """Return (proj, sdk_or_none) for the requested graph."""
    uri = _resolve_uri(args.uri)
    graph_name = args.graph
    from tortoise.projection import FalkorProjection
    from tortoise.sdk import TortoiseSDK
    if uri.startswith(("docker://", "redis://", "rediss://")):
        from urllib.parse import urlparse
        if graph_name == "tortoise":
            graph_name = urlparse(uri).path.lstrip("/") or "tortoise"
        proj = FalkorProjection.from_uri(uri, graph_name=graph_name)
        sdk = None
        if args.merge:
            # TortoiseSDK resolves URIs ONLY via the TORTOISE_DB_URI env var
            # (sdk.py __init__: the URI branch fires when db_path is None).
            # Passing the URI string positionally lands in db_path and gets
            # treated as an embedded file path ('Relative DB path docker://…
            # rejected'). Pass the resolved URI through the env instead
            # (graph-scripts/decide.py pattern) so the SDK targets the hosted
            # store — and never constructs/busy-probes the default embedded DB.
            os.environ["TORTOISE_DB_URI"] = uri
            sdk = TortoiseSDK()
            # Pin the SDK's projection to the requested graph: with no
            # namespace the SDK derives the graph from the URI path only
            # (sdk.py _get_proj) — matches --graph tortoise but NOT an
            # explicit --graph team_<id>/tortoise_test_*. Scan and supersede
            # must hit the SAME graph.
            sdk._proj = proj
    else:
        proj = FalkorProjection(uri, graph_name=graph_name)
        sdk = TortoiseSDK(uri, namespace=None) if args.merge else None
    return proj, sdk


def scan_observation_duplicates(proj) -> dict:
    """Find unkeyed observation points with a keyed statement twin (same
    github_url). Returns {url: {"observations": [ids], "statements": [ids]}}."""
    obs_rows = proj.g.query(
        "MATCH (n:Point {pointKind:'observation'}) "
        "WHERE n.github_url IS NOT NULL AND n.github_url <> '' "
        "RETURN n.id, n.github_url, n.status",
    ).result_set
    stmt_rows = proj.g.query(
        "MATCH (n:Point {pointKind:'statement'}) "
        "WHERE n.github_url IS NOT NULL AND n.github_url <> '' "
        "AND (n.status IS NULL OR NOT (n.status IN ['superseded','retracted','archived'])) "
        "RETURN n.id, n.github_url, n.status",
    ).result_set
    statements_by_url: dict[str, list[str]] = {}
    for pid, url, _status in stmt_rows:
        statements_by_url.setdefault(str(url), []).append(str(pid))
    pairs: dict[str, dict] = {}
    for pid, url, _status in obs_rows:
        url = str(url)
        twins = statements_by_url.get(url)
        if not twins:
            continue  # no keyed twin — best-effort: leave as-is
        prior = pairs.setdefault(url, {"observations": [], "statements": twins})
        prior["observations"].append(str(pid))
    return pairs


def dry_run_report(proj) -> dict:
    """Report-only scan: counts + per-url pairing (NO writes)."""
    pairs = scan_observation_duplicates(proj)
    return {
        "duplicate_urls": len(pairs),
        "observations_to_supersede": sum(
            len(v["observations"]) for v in pairs.values()),
        "pairs": pairs,
    }


def merge_duplicates(sdk, proj, *, dry_run: bool = True) -> dict:
    """Supersede each duplicate observation into its statement twin.

    dry_run=True → report only. dry_run=False → supersede_point(observation,
    statement) per pair (CORRECTS + edge transfer + bi-temporal). The
    observation becomes terminal; the statement stays current.
    """
    pairs = scan_observation_duplicates(proj)
    merged = 0
    skipped: list[str] = []
    if not dry_run:
        for url, info in pairs.items():
            stmt_id = info["statements"][0]  # 1:1 issue↔statement
            for obs_id in info["observations"]:
                try:
                    sdk.supersede_point(obs_id, stmt_id)
                    merged += 1
                except Exception as e:
                    skipped.append(f"{url}: {e}")
    return {
        "duplicate_urls": len(pairs),
        "observations_to_supersede": sum(
            len(v["observations"]) for v in pairs.values()),
        "merged": merged if not dry_run else 0,
        "dry_run": dry_run,
        "skipped": skipped,
    }


def test_guard(graph_name: str, yes: bool = False) -> None:
    """Safety gate: confirm before running on non-test graphs (mirrors
    backfill_is_episodic's guard)."""
    if graph_name.startswith("tortoise_test_") or graph_name.startswith("test_"):
        print(f"✅ Test graph detected ({graph_name}) — proceeding")
        return
    if yes:
        print(f"⚠️  Target graph ({graph_name}) — --yes flag set, proceeding")
        return
    print(f"\n⚠️  Target graph is '{graph_name}' — NOT a test graph.")
    print("    This script supersedes duplicate `observation` points into")
    print("    their keyed `statement` twins (best-effort, deliver-or-defer).")
    print("    Run with --yes to confirm, or use a test-prefixed graph.")
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="#1714: dedup legacy observation points into keyed statements")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only (DEFAULT — no writes)")
    ap.add_argument("--merge", action="store_true",
                    help="opt-in: supersede duplicate observations into their "
                         "statement twins (NOT the default — deliver-or-defer)")
    ap.add_argument("--yes", action="store_true",
                    help="skip confirmation (required for non-test graphs)")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", ""))
    ap.add_argument("--graph", default="tortoise",
                    help="graph name (hosted: per-tenant graph, e.g. team_<id>)")
    args = ap.parse_args()

    if not args.merge:
        args.dry_run = True

    test_guard(args.graph, args.yes)

    proj, sdk = _connect(args)
    try:
        if args.dry_run:
            report = dry_run_report(proj)
            print(
                f"[dry-run] {report['observations_to_supersede']} duplicate "
                f"observation(s) across {report['duplicate_urls']} issue url(s) "
                f"(graph: {args.graph}) — no writes (deliver-or-defer default)")
            for url, info in sorted(report["pairs"].items()):
                print(f"  {url}: observations={info['observations']} "
                      f"→ statement={info['statements']}")
            return 0
        report = merge_duplicates(sdk, proj, dry_run=False)
        print(
            f"[merge] superseded {report['merged']} duplicate observation(s) "
            f"into their statement twins (graph: {args.graph})")
        for sk in report["skipped"]:
            print(f"  skipped: {sk}")
        return 0
    finally:
        proj.close()
        if sdk is not None:
            sdk.close()


if __name__ == "__main__":
    sys.exit(main())
