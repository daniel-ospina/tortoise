#!/usr/bin/env python3
"""S2 census — is truth<->cache divergence OBSERVED, or merely checkable?

Counts PRODUCTION (non-test) call sites of the three modules the two-store
chain needs wired together:

  * tortoise/reconciliation.py::reconcile      — compares the two stores
  * tortoise/record_projection.py::replay      — journal -> Postgres
  * tortoise/cache_projection.py::read_record_view — Postgres -> cache input
  * FalkorProjection.rebuild_from_record       — the cache rebuild

A checkable mechanism with zero production callers cannot observe anything.
Populations are stated (which directories were searched).
"""
from __future__ import annotations

import pathlib
import re

PROD_DIRS = ["tortoise", "tools", "scripts"]
TESTS = pathlib.Path("tests")
TARGETS = {
    "reconciliation.reconcile": re.compile(r"reconciliation import reconcile|from \.reconciliation import|reconciliation\.reconcile\("),
    "record_projection.replay": re.compile(r"record_projection\.replay\(|\brp\.replay\(|from tortoise.record_projection import[^\n]*\breplay\b"),
    "cache_projection.read_record_view": re.compile(r"read_record_view\("),
    "FalkorProjection.rebuild_from_record": re.compile(r"\.rebuild_from_record\("),
}


def census(roots, pattern):
    hits = []
    for root in roots:
        for f in pathlib.Path(root).rglob("*.py"):
            if "__pycache__" in str(f):
                continue
            t = f.read_text(errors="replace")
            for i, line in enumerate(t.splitlines(), 1):
                if pattern.search(line) and not line.strip().startswith("#") \
                        and not re.match(r"\s*def ", line):
                    hits.append(f"{f}:{i}: {line.strip()[:100]}")
    return hits


print(f"production population: {PROD_DIRS}/**/*.py")
for name, pat in TARGETS.items():
    hits = census(PROD_DIRS, pat)
    print(f"\n== {name}: {len(hits)} production call site(s) ==")
    for h in hits:
        print(f"  {h}")

print(f"\ntest population: {TESTS}/*.py")
for name, pat in TARGETS.items():
    hits = census([str(TESTS)], pat)
    print(f"  {name}: {len(hits)} test-file hit line(s)")
