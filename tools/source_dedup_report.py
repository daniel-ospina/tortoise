#!/usr/bin/env python3
"""Source dedup report — the measurement for issue #5012 (S0a/S0b).

Answers the issue's cheap query exactly:

    group :Source nodes by their *canonical* identity, count > 1

and reports it against the two baselines the change moves:

    raw URL        — what the write path keys on BEFORE S0a/S0b
    canonical URL  — the S0a identity S0b registers by

The delta is the volume lever: each duplicate source would otherwise pull in
its own ``extractedFrom`` chain (~6.6 derived rows/source, measured).

Usage
-----
    # against the graph in $TORTOISE_DB_URI (or the default embedded path)
    python3 tools/source_dedup_report.py

    # machine-readable
    python3 tools/source_dedup_report.py --json

    # adopt legacy nodes (set canonicalUrl + urlAliases) — idempotent, additive
    python3 tools/source_dedup_report.py --backfill

Evidence discipline (methodology 1): the report prints a real sample of the
duplicate groups it found, not just a total, so the claim can be checked.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.source_identity import normalize_source_url


def _load_sources(sdk) -> list[dict]:
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source) "
        "RETURN s.url, s.canonicalUrl, s.urlAliases, s.contentHash, s.id"
    ).result_set
    out = []
    for url, canonical, aliases, content_hash, sid in rows:
        out.append({
            "url": url or "",
            "canonicalUrl": canonical,
            "urlAliases": aliases or [],
            "contentHash": content_hash or "",
            "id": sid or "",
        })
    return out


def _groups(sources: list[dict], key_fn) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    for s in sources:
        k = key_fn(s)
        if k:
            g[k].append(s)
    return g


def _dup_stats(groups: dict[str, list[dict]]) -> dict:
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    redundant = sum(len(v) - 1 for v in dups.values())
    return {
        "groups": len(groups),
        "duplicate_groups": len(dups),
        "redundant_nodes": redundant,
        "examples": dups,
    }


def _effective_identity(src: dict) -> str:
    """The identity S0b WOULD use: stored canonicalUrl, else S0a on the url."""
    return src.get("canonicalUrl") or normalize_source_url(src.get("url", ""))


def build_report(sources: list[dict], sample: int = 20) -> dict:
    raw = _dup_stats(_groups(sources, lambda s: s["url"]))
    canon = _dup_stats(_groups(sources, _effective_identity))
    # Mirrors: different canonical URL, identical non-empty content hash.
    mirrors = _dup_stats(
        _groups(
            [s for s in sources if s["contentHash"]],
            lambda s: s["contentHash"],
        )
    )
    return {
        "total_sources": len(sources),
        "raw_url": _trim(raw, sample),
        "canonical_url": _trim(canon, sample),
        "content_hash_mirrors": _trim(mirrors, sample),
        "collapse": {
            "redundant_nodes_removable": canon["redundant_nodes"],
            "distinct_after_dedup": canon["groups"],
            "reduction_pct": (
                round(100.0 * canon["redundant_nodes"] / len(sources), 2)
                if sources else 0.0
            ),
        },
    }


def _trim(stats: dict, sample: int) -> dict:
    examples = []
    for k, members in list(stats["examples"].items())[:sample]:
        examples.append({
            "identity": k,
            "count": len(members),
            "urls": [m["url"] for m in members],
        })
    return {
        "duplicate_groups": stats["duplicate_groups"],
        "redundant_nodes": stats["redundant_nodes"],
        "examples": examples,
    }


def _print_human(report: dict) -> None:
    print("=" * 72)
    print(f"SOURCE DEDUP REPORT — {report['total_sources']} :Source nodes")
    print("=" * 72)
    for label, key in (("RAW URL (pre-S0a/S0b)", "raw_url"),
                       ("CANONICAL URL (post-S0b)", "canonical_url"),
                       ("CONTENT-HASH MIRRORS", "content_hash_mirrors")):
        st = report[key]
        print(f"\n{label}")
        print(f"  duplicate groups : {st['duplicate_groups']}")
        print(f"  redundant nodes  : {st['redundant_nodes']}")
        for ex in st["examples"][:10]:
            print(f"    ×{ex['count']}  {ex['identity']}")
            for u in ex["urls"][:4]:
                print(f"        - {u}")
    c = report["collapse"]
    print("\n" + "-" * 72)
    print(f"LEVER: {c['redundant_nodes_removable']} redundant :Source nodes "
          f"({c['reduction_pct']}% of sources) collapse to "
          f"{c['distinct_after_dedup']} distinct identities.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--backfill", action="store_true",
                    help="adopt legacy nodes: set canonicalUrl + urlAliases")
    ap.add_argument("--sample", type=int, default=20,
                    help="max duplicate groups to show (default 20)")
    args = ap.parse_args(argv)

    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK()
    try:
        sources = _load_sources(sdk)
        if args.backfill:
            adopted = 0
            for s in sources:
                if s["canonicalUrl"]:
                    continue
                cu = normalize_source_url(s["url"])
                if not cu:
                    continue
                sdk._get_proj().g.query(
                    "MATCH (s:Source {url:$url}) SET s.canonicalUrl=$cu, "
                    "s.urlAliases = CASE WHEN $url IN coalesce(s.urlAliases,[]) "
                    "  THEN coalesce(s.urlAliases,[]) "
                    "  ELSE coalesce(s.urlAliases,[]) + [$url] END",
                    params={"url": s["url"], "cu": cu},
                )
                adopted += 1
            if not args.json:
                print(f"backfilled canonicalUrl on {adopted} legacy Source node(s)")
            sources = _load_sources(sdk)
        report = build_report(sources, sample=args.sample)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_human(report)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
