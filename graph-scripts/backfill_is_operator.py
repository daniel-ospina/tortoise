#!/usr/bin/env python3
"""Backfill is_operator = false on Points missing the property (#522).

Semantic decision (issue #522): NULL means non-operator. After this
backfill, every Point has an explicit boolean, so queries can use the
indexable `n.is_operator = false` form instead of the previous unindexable
`(n.is_operator IS NULL OR n.is_operator = false)` disjunction (full
Node By Label Scan on every Point).

Also repairs legacy stale composite Point indexes: an older FalkorDB build
created a composite `:Point(id, pointKind, content_hash, content,
is_operator)` index whose boolean-false lookups silently miss (verified on
a 19k-point DB — `= false` returned 0 while `<> true` returned the full
non-operator set). The composite is dropped and the canonical per-property
indexes are recreated, matching projection `_ensure_indexes` — with one
exception: on embedded falkordblite/redislite, `is_operator` is NOT
recreated (the persisted bool type table degrades across reopen, so the
indexed `= false` form silently returns 0 — the code drops it on open;
see _ensure_indexes / #522 / #1015). The backfill therefore gates the
is_operator index recreation on non-embedded backends only.

Requires #327's Point.is_operator RANGE index (already on main).

Idempotent: only touches Points where is_operator IS NULL; index drop is
best-effort. Safe to re-run.

Usage:
    TORTOISE_DB_URI=docker://:@localhost:16379/tortoise \
        python3 graph-scripts/backfill_is_operator.py [--dry-run] [--yes]

Test safety: always verify graph name before running. For tests, use
test-prefixed graphs (tortoise_test_*) and pass --yes to skip confirmation.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK  # noqa: I001


# Legacy composite index (older FalkorDB build) whose boolean-false
# lookups are broken — dropped during backfill so `= false` queries
# return correct results (#522).
_LEGACY_COMPOSITE_INDEX = (
    "DROP INDEX ON :Point(id, pointKind, content_hash, content, is_operator)"
)


# Canonical per-property Point indexes — mirror projection._ensure_indexes.
# is_operator is deliberately NOT in the embedded-safe set: falkordblite/
# redislite degrades the persisted bool type table across reopen, so an
# indexed `= false` silently returns 0 (see _ensure_indexes / #522 / #1015).
# On embedded the recreate below is skipped (and _ensure_indexes drops any
# stale is_operator index on open); non-embedded gets the #522 perf index.
_CANONICAL_POINT_PROPS = ("id", "pointKind", "content_hash")
_NON_EMBEDDED_ONLY_PROPS = ("is_operator",)


def test_guard(graph_name: str, yes: bool = False) -> None:
    """Safety gate: confirm before running on non-test graphs."""
    if graph_name.startswith("tortoise_test_") or graph_name.startswith("test_"):
        print(f"✅ Test graph detected ({graph_name}) — proceeding")
        return
    if yes:
        print(f"⚠️  Production graph ({graph_name}) — --yes flag set, proceeding")
        return
    print(f"\n⚠️  Target graph is '{graph_name}' — NOT a test graph.")
    print("    This script stamps is_operator=false on ALL Points missing the property.")
    print("    Run with --yes to confirm, or use a test-prefixed graph.")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Backfill is_operator=false on Points (#522)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing")
    parser.add_argument("--yes", action="store_true",
                        help="Skip confirmation on non-test graphs")
    args = parser.parse_args()

    sdk = TortoiseSDK(db_path=None, namespace=None)
    proj = sdk._get_proj()

    graph_name = getattr(proj, "graph_name", None) or "tortoise"
    test_guard(str(graph_name), args.yes)

    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator IS NULL "
        "RETURN count(n)"
    ).result_set
    total = rows[0][0] if rows else 0
    print(f"Points missing is_operator: {total}")

    if args.dry_run:
        print("\n[Dry-run] No changes written.")
        sdk.close()
        return

    if total > 0:
        # Legacy operators: points with op_type but no is_operator property are
        # documented as operators (projection is_op rule: bool(is_operator or
        # op_type)) — stamp them TRUE, not false, so the backfill never
        # demotes them (#522 review P2). op_type is the reliable signal;
        # edge-existence checks are redundant with it for IMPL/NAND ops.
        legacy_ops = proj.g.query(
            "MATCH (n:Point) WHERE n.is_operator IS NULL "
            "AND n.op_type IS NOT NULL "
            "RETURN count(n)"
        ).result_set
        n_legacy_ops = legacy_ops[0][0] if legacy_ops else 0
        if n_legacy_ops:
            proj.g.query(
                "MATCH (n:Point) WHERE n.is_operator IS NULL "
                "AND n.op_type IS NOT NULL "
                "SET n.is_operator = true"
            )
            print(f"Stamped {n_legacy_ops} legacy operator Points as is_operator=true.")
        proj.g.query(
            "MATCH (n:Point) WHERE n.is_operator IS NULL "
            "SET n.is_operator = false"
        )
        print(f"Backfilled {total - n_legacy_ops} non-operator Points.")

    # Repair legacy composite index (best-effort — may not exist on
    # fresh DBs or on FalkorDB builds without composite-index support).
    try:
        proj.g.query(_LEGACY_COMPOSITE_INDEX)
        print("Dropped legacy composite Point index (stale boolean-false lookups).")
    except Exception as e:
        msg = str(e).lower()
        if "no such index" in msg or "already" in msg or "does not exist" in msg:
            print("No legacy composite index to drop.")
        else:
            # The rewrite's correctness depends on the drop succeeding — a
            # silently-kept stale index makes every `= false` query return
            # empty results. Fail loudly rather than report false success.
            print(f"❌ Could not drop legacy composite index: {e}")
            print("   `= false` queries will return wrong results until this is fixed.")
            sdk.close()
            sys.exit(1)

    # Recreate canonical per-property indexes (idempotent — errors on
    # already-indexed are expected and ignored). is_operator is recreated
    # on non-embedded only — embedded drops it on open (bool type-table
    # degradation, see _ensure_indexes).
    for prop in _CANONICAL_POINT_PROPS:
        try:  # noqa: SIM105
            proj.g.query(f"CREATE INDEX FOR (n:Point) ON (n.{prop})")
        except Exception:
            pass  # already indexed — expected
    if not getattr(proj, "_is_embedded", False):
        for prop in _NON_EMBEDDED_ONLY_PROPS:
            try:  # noqa: SIM105
                proj.g.query(f"CREATE INDEX FOR (n:Point) ON (n.{prop})")
            except Exception:
                pass  # already indexed — expected

    # Verify: no NULLs remain
    after = proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator IS NULL RETURN count(n)"
    ).result_set
    remaining = after[0][0] if after else 0
    print(f"Remaining NULL: {remaining}")
    if remaining > 0:
        print("⚠️  Some Points still missing is_operator — investigate.")
        sdk.close()
        sys.exit(1)

    # Sanity: the whole point of #522 is that `= false` returns the full
    # non-operator population. On docker FalkorDB (hosted production) the
    # boolean comparison always works post-backfill. On embedded redislite,
    # indexed `= false` is unreliable (the is_operator index is dropped on
    # open — #1015/#1069) but the label-scan form is correct, so a post-
    # backfill divergence here means either a stale index survived or a
    # legacy string-typed attribute table persists — warn loudly with
    # remediation instead of blocking.
    false_count = proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator = false RETURN count(n)"
    ).result_set[0][0]
    not_true_count = proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator <> true RETURN count(n)"
    ).result_set[0][0]
    total_points = proj.g.query(
        "MATCH (n:Point) RETURN count(n)"
    ).result_set[0][0]
    if false_count != not_true_count or (false_count == 0 and total_points > 0):
        print(f"⚠️  Sanity divergence: `= false` matches {false_count}, "
              f"`<> true` matches {not_true_count} (total {total_points}).")
        print("    On embedded redislite this is the legacy string-typed attribute "
              "table (see #522); docker FalkorDB is unaffected. Remediation: "
              "export + rebuild the graph, or re-import via the JSONL event log.")
    else:
        print(f"✅ Sanity check passed: `= false` matches {false_count} non-operators "
              f"({not_true_count} via `<> true`).")

    sdk.close()


if __name__ == "__main__":
    main()
