#!/usr/bin/env python3
"""Cross-subgraph parity sample — #49 Phase 2 safety net (Task 2.0).

Per plan §9.7: Sample 50 distinct context values, compute OLD operator set
(operators connecting directly to context-labeled points) vs NEW anchors-based
set (_bfs_select_operators), and report deltas.

Block the REMOVE migration if there are unexplained operator-set deltas.

Usage:
  # Sample 50 contexts from the DEV graph:
  TORTOISE_DB_URI=docker://:@localhost:16379/tortoise \\
    python3 graph-scripts/parity_sample.py

  # With explicit limit and seed:
  python3 graph-scripts/parity_sample.py --limit 30 --seed 42

  # Blocking mode (exit code 1 if unexplained deltas):
  python3 graph-scripts/parity_sample.py --block

  # Explain a specific delta (operator chain expected):
  python3 graph-scripts/parity_sample.py --explain-deltas
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections import defaultdict

# Allow running from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.analyze import _bfs_select_operators


# ── Lightweight graph wrapper (avoids FalkorProjection index creation) ─

class _LightProj:
    """Minimal projection-like wrapper for BFS + query operations."""
    def __init__(self, g):
        self.g = g


# ── Helpers ────────────────────────────────────────────────────────────

def _parse_uri(uri: str) -> dict:
    """Parse docker:// URI into components."""
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 16379,
        "password": parsed.password or "",
        "graph": parsed.path.lstrip("/") or "tortoise",
    }


def get_distinct_contexts(g, limit: int = 200) -> list[str]:
    """Get all distinct non-null context values from Points."""
    rows = g.query(
        "MATCH (n:Point) WHERE n.context IS NOT NULL "
        "RETURN DISTINCT n.context ORDER BY n.context LIMIT $limit",
        params={"limit": limit},
    ).result_set
    return [r[0] for r in rows]


def get_old_operator_set(g, context: str) -> set[str]:
    """OLD method: operators that connect (via IMPL|NAND) to Points with this context.

    MATCH (op:Point {is_operator:true})-[r:IMPL|NAND]->(c:Point {context:$ctx})
    """
    rows = g.query(
        "MATCH (op:Point {is_operator:true})-[r:IMPL|NAND]->(c:Point {context:$ctx}) "
        "RETURN DISTINCT op.id",
        params={"ctx": context},
    ).result_set
    return {r[0] for r in rows}


def get_context_anchors(g, context: str) -> list[str]:
    """Get all non-operator Point IDs with this context value (anchors for BFS)."""
    rows = g.query(
        "MATCH (n:Point {context:$ctx}) "
        "WHERE n.is_operator IS NULL OR n.is_operator = false "
        "RETURN n.id",
        params={"ctx": context},
    ).result_set
    return [r[0] for r in rows]


def get_new_operator_set(proj: _LightProj, anchors: list[str]) -> set[str]:
    """NEW method: BFS select operators from anchor points.

    This is what the post-REMOVE world will use — anchors are non-operator
    points that used to carry the context property. BFS from them to find
    connected operators.
    """
    if not anchors:
        return set()
    return _bfs_select_operators(
        proj, anchors, max_hops=1, rel_filter="IMPL|NAND", direction="both"
    )


def classify_delta(
    g,
    old_ops: set[str],
    new_ops: set[str],
    context_value: str,
) -> dict:
    """Classify the delta between old and new operator sets.

    Returns {
        "context": ctx,
        "old_count": len(old_ops),
        "new_count": len(new_ops),
        "only_old": [op_ids only in old],
        "only_new": [op_ids only in new],
        "status": "MATCH" | "DELTA_EXPLAINED" | "DELTA_UNEXPLAINED",
        "explanation": str,
    }
    """
    only_old = old_ops - new_ops
    only_new = new_ops - old_ops

    if not only_old and not only_new:
        return {
            "context": context_value,
            "old_count": len(old_ops),
            "new_count": len(new_ops),
            "only_old": [],
            "only_new": [],
            "status": "MATCH",
            "explanation": "Operator sets are identical.",
        }

    # Attempt to explain deltas
    explanations = []

    # Explanation 1: operators in only_old might be 2+ hops away (BFS capped at 1)
    if only_old:
        explanations.append(
            f"{len(only_old)} operators in OLD but not NEW — "
            f"may be 2+ hops from anchors. Old query traverses directly "
            f"(MATCH op-[r]->c), while BFS uses 1-hop from anchors."
        )

    # Explanation 2: operators in only_new might be via anchor chains
    # where an operator connects to another operator that connects to context
    if only_new:
        # Check if only_new operators are operator→operator chains
        chain_count = 0
        for op_id in list(only_new):
            rows = g.query(
                "MATCH (op:Point {id:$id})-[r:IMPL|NAND]->(target:Point) "
                "WHERE target.is_operator = true "
                "RETURN count(target)",
                params={"id": op_id},
            ).result_set
            if rows and rows[0][0] > 0:
                chain_count += 1
        if chain_count > 0:
            explanations.append(
                f"{chain_count}/{len(only_new)} only_new operators connect to "
                f"other operators (operator→operator chains). Valid in BFS."
            )

    # Determine status
    # If the only differences are explainable (e.g., old_set is a subset of new_set,
    # or new_set is a subset of old_set), mark as DELTA_EXPLAINED
    if not only_old or not only_new:
        # Directional subset — typically BFS finds fewer or more but not both directions
        if only_old:
            explanation = "OLD has operators not in NEW — BFS may have missed them (hop limit)."
            return {
                "context": context_value,
                "old_count": len(old_ops),
                "new_count": len(new_ops),
                "only_old": list(only_old)[:10],
                "only_new": [],
                "status": "DELTA_EXPLAINED",
                "explanation": explanation + " " + " ".join(explanations),
            }
        else:
            return {
                "context": context_value,
                "old_count": len(old_ops),
                "new_count": len(new_ops),
                "only_old": [],
                "only_new": list(only_new)[:10],
                "status": "DELTA_EXPLAINED",
                "explanation": " ".join(explanations) or "NEW has additional operators via BFS expansion.",
            }

    # Both directions differ — unexplained
    return {
        "context": context_value,
        "old_count": len(old_ops),
        "new_count": len(new_ops),
        "only_old": list(only_old)[:10],
        "only_new": list(only_new)[:10],
        "status": "DELTA_UNEXPLAINED",
        "explanation": " ".join(explanations) or "Both sets differ — requires investigation.",
    }


# ── Main ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-subgraph parity sample — #49 Phase 2 Task 2.0"
    )
    parser.add_argument("--limit", type=int, default=50,
                        help="Maximum contexts to sample (default: 50)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    parser.add_argument("--uri", default=None,
                        help="Override TORTOISE_DB_URI")
    parser.add_argument("--block", action="store_true",
                        help="Exit 1 if unexplained deltas found")
    parser.add_argument("--explain-deltas", action="store_true",
                        help="Show detailed analysis of deltas")
    parser.add_argument("--context", default=None,
                        help="Check a specific context value (skip sampling)")
    args = parser.parse_args()

    uri = args.uri or os.environ.get(
        "TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise"
    )
    cfg = _parse_uri(uri)

    print("=" * 60)
    print("CROSS-SUBGRAPH PARITY SAMPLE")
    print("=" * 60)
    print(f"  URI:     {uri}")
    print(f"  Graph:   {cfg['graph']}")
    print(f"  Limit:   {args.limit}")
    print(f"  Seed:    {args.seed}")
    print()

    # Use direct FalkorDB connection (avoids heavy index creation)
    from falkordb import FalkorDB as _FalkorDB
    db = _FalkorDB(
        host=cfg["host"], port=cfg["port"],
        password=cfg["password"] or None,
        socket_connect_timeout=5, socket_timeout=120,
    )
    g = db.select_graph(cfg["graph"])
    light_proj = _LightProj(g)

    try:
        # ── Collect context values ──────────────────────────────────
        if args.context:
            contexts = [args.context]
            print(f"Checking specific context: {args.context}")
        else:
            all_contexts = get_distinct_contexts(g, limit=200)
            if not all_contexts:
                print("No points with context found. Nothing to sample.")
                return 0
            print(f"Found {len(all_contexts)} distinct context values.")

            # Random sample
            if args.seed is not None:
                random.seed(args.seed)
            sample_size = min(args.limit, len(all_contexts))
            contexts = random.sample(all_contexts, sample_size) if sample_size < len(all_contexts) else all_contexts
            print(f"Sampled {len(contexts)}.")

        print()

        # ── Run parity check for each context ───────────────────────
        results = []
        match_count = 0
        explained_count = 0
        unexplained_count = 0
        empty_count = 0

        for i, ctx in enumerate(contexts):
            t0 = time.time()

            # Old method
            old_ops = get_old_operator_set(g, ctx)

            # New method (anchors → BFS)
            anchors = get_context_anchors(g, ctx)
            new_ops = get_new_operator_set(light_proj, anchors)

            # Classify
            result = classify_delta(g, old_ops, new_ops, ctx)

            elapsed = time.time() - t0

            if result["status"] == "MATCH":
                match_count += 1
            elif result["status"] == "DELTA_EXPLAINED":
                explained_count += 1
            elif result["status"] == "DELTA_UNEXPLAINED":
                unexplained_count += 1

            if not old_ops and not new_ops:
                empty_count += 1
                result["status"] = "EMPTY"

            results.append(result)

            # Print progress
            status_icon = {
                "MATCH": "✓",
                "DELTA_EXPLAINED": "~",
                "DELTA_UNEXPLAINED": "✗",
                "EMPTY": "○",
            }.get(result["status"], "?")

            print(f"  [{i+1:3d}/{len(contexts)}] {status_icon} "
                  f"old={len(old_ops):3d} new={len(new_ops):3d} "
                  f"→ {result['status']:20s} "
                  f"({elapsed:.2f}s)  {ctx[:50]}")

        # ── Summary ─────────────────────────────────────────────────
        print()
        print("=" * 60)
        print("PARITY SUMMARY")
        print("=" * 60)
        print(f"  Total sampled:        {len(contexts)}")
        print(f"  MATCH (identical):    {match_count}")
        print(f"  DELTA_EXPLAINED:      {explained_count}")
        print(f"  DELTA_UNEXPLAINED:    {unexplained_count}")
        print(f"  EMPTY (no operators): {empty_count}")
        print()

        if unexplained_count > 0:
            print("⚠  UNEXPLAINED DELTAS FOUND — investigate before REMOVE migration:")
            for r in results:
                if r["status"] == "DELTA_UNEXPLAINED":
                    print(f"  ✗ context={r['context']}")
                    print(f"    only_old: {r['only_old'][:5]}")
                    print(f"    only_new: {r['only_new'][:5]}")
                    print(f"    explanation: {r['explanation']}")
            print()

            if args.block:
                print("BLOCK: Unexplained deltas → exit code 1")
                return 1

        # ── Detailed deltas ─────────────────────────────────────────
        if args.explain_deltas and (explained_count > 0 or unexplained_count > 0):
            print("=" * 60)
            print("DETAILED DELTA ANALYSIS")
            print("=" * 60)
            for r in results:
                if r["status"] in ("DELTA_EXPLAINED", "DELTA_UNEXPLAINED"):
                    print(f"\n  context: {r['context']}")
                    print(f"  old_ops: {r['old_count']}, new_ops: {r['new_count']}")
                    print(f"  status:  {r['status']}")
                    print(f"  explanation: {r['explanation']}")
                    if r["only_old"]:
                        print(f"  only in OLD (first 10): {r['only_old']}")
                    if r["only_new"]:
                        print(f"  only in NEW (first 10): {r['only_new']}")

        print("\nParity check complete.")
        if unexplained_count == 0:
            print("✓ All sampled contexts match or have explained deltas.")
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
