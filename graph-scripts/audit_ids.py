#!/usr/bin/env python3
"""Audit Tortoise Point ID formats — catalogs all ID schemes in the graph.

Usage:
  TORTOISE_DB_URI=docker://:@localhost:16379/tortoise python3 graph-scripts/audit_ids.py

FalkorDB does not support =~ regex in Cypher, so all categorization is client-side
after fetching IDs.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK
from tortoise.projection import FalkorProjection

URI = os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise")

# ── Canonical ULID pattern (from tortoise/ids.py: ts-hex + "-" + uuid12) ──
ULID_RE = re.compile(r"^[0-9a-f]+-[0-9a-f]{12}$")

# ── Standard ULID (Crockford base32, 26 chars) — e.g., 01KXGMG4FZJDDACP918R1MX11Y ──
CROCKFORD_ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")

# ── Legacy prefix map (ordered: more-specific first, catch-all last) ──
LEGACY_PREFIXES = [
    ("letta-", "letta-*"),
    ("EVENTS_", "EVENTS_*"),
    ("EP_PASS_", "EP_PASS_*"),
    ("RESCORE_", "RESCORE_*"),
    ("STRATEGY", "STRATEGY*"),
    ("NARY_", "NARY_*"),
    ("ARCH_", "ARCH_*"),
    ("SVBP_", "SVBP_*"),
    ("GATE", "GATE*"),
    ("op-", "op-*"),
    ("UC", "UC*"),
]


def categorize(pid: str) -> str:
    """Return the category label for a single Point ID."""
    if ULID_RE.match(pid):
        return "ULID (canonical)"
    if CROCKFORD_ULID_RE.match(pid):
        return "ULID (Crockford)"
    if re.match(r"^19fc[0-9a-f]+$", pid):
        return "19fc-hash"
    if pid.isdigit():
        return "numeric"
    for prefix, label in LEGACY_PREFIXES:
        if pid.startswith(prefix):
            return label
    # Catch-all: "C*" — starts with C followed by digit (not a ULID C-prefix in UUID part)
    if re.match(r"^C\d", pid):
        return "C*"
    return "other"


def main():
    sdk = TortoiseSDK()
    sdk._proj = FalkorProjection.from_uri(URI)
    proj = sdk._proj

    print("Fetching Point IDs from graph …")
    result = proj.g.query("MATCH (n:Point) RETURN n.id", timeout=30000).result_set
    all_ids = [row[0] for row in result]
    total = len(all_ids)
    print(f"Total Points: {total}\n")

    # ── Categorize ──
    buckets: dict[str, list[str]] = {}
    for pid in all_ids:
        cat = categorize(pid)
        buckets.setdefault(cat, []).append(pid)

    # ── Ordered output ──
    order = [
        "ULID (canonical)",
        "ULID (Crockford)",
        "19fc-hash",
        "numeric",
        "letta-*",
        "op-*",
        "ARCH_*",
        "SVBP_*",
        "STRATEGY*",
        "EVENTS_*",
        "NARY_*",
        "RESCORE_*",
        "EP_PASS_*",
        "GATE*",
        "UC*",
        "C*",
        "other",
    ]

    for cat in order:
        ids = buckets.get(cat, [])
        if not ids:
            continue
        pct = (len(ids) / total) * 100
        print(f"{cat}: {len(ids)} ({pct:.1f}%)")
        sample = ids[:5]
        for s in sample:
            print(f"  {s}")
        if len(ids) > 5:
            print(f"  … and {len(ids) - 5} more")
        print()

    # ── Summary ──
    canonical = len(buckets.get("ULID (canonical)", []))
    crockford = len(buckets.get("ULID (Crockford)", []))
    print(f"{'='*50}")
    print(f"Total: {total} | Canonical ULID: {canonical} "
          f"({(canonical/total)*100:.1f}%)")
    print(f"Crockford ULID: {crockford} ({(crockford/total)*100:.1f}%)")
    print(f"Non-ULID: {total - canonical - crockford}")
    print(f"Categories found: {len(buckets)}")


if __name__ == "__main__":
    main()
