"""Tests for entity-scoped tortoise_analyze (GAP-02 #6988)."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100


def _tmp_db() -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_test_"), "test.db")


def _seed_small_graph(sdk: TortoiseSDK) -> tuple[str, str, str]:
    """Create a small graph: entity A + B connected via NAND, plus an outsider C."""
    a = sdk.create_point("statement", "Entity A says X is true")["id"]
    b = sdk.create_point("statement", "Entity B says X is false")["id"]
    c = sdk.create_point("statement", "Entity C is unrelated")["id"]

    # Connect A and B via NAND (disagreement)
    proj = sdk._get_proj()
    op_id = f"nand_{a}_{b}"
    proj.g.query(
        "CREATE (op:Point {id:$id, op_type:'NAND', is_operator:true, "
        "content:'NAND operator'})",
        params={"id": op_id},
    )
    proj.g.query(
        "MATCH (a:Point {id:$a}), (op:Point {id:$op}), (b:Point {id:$b}) "
        "CREATE (a)<-[:NAND]-(op)-[:NAND]->(b)",
        params={"a": a, "op": op_id, "b": b},
    )

    return a, b, c


# ── Core tests ──────────────────────────────────────────────────────

def test_entity_scoped_only_sees_subgraph():
    """Scoped analysis on entity A only returns results from A's subgraph (A+B, not C)."""
    db_path = _tmp_db()
    sdk = TortoiseSDK(db_path)
    a_id, b_id, c_id = _seed_small_graph(sdk)  # noqa: RUF059

    from tortoise.analyze import analyze

    # Subgraph: only A (no connected nodes with 0 hops, or just A if we pass just A)
    # We scope to A (no hops — just A itself, no connected B yet)
    result = analyze(
        "where is the disagreement",
        sdk._get_proj(),
        entity_subgraph_ids={a_id},
    )
    # A alone has no NAND partners reachable, so no disagreement found
    assert "No conflicts found" in result["answer"], \
        f"expected no conflicts for singleton subgraph, got: {result['answer']}"


def test_entity_scoped_sees_neighbors():
    """Scoped analysis including neighbors (A+B) sees the disagreement."""
    db_path = _tmp_db()
    sdk = TortoiseSDK(db_path)
    a_id, b_id, c_id = _seed_small_graph(sdk)  # noqa: RUF059

    from tortoise.analyze import analyze

    # Subgraph: {A, B}
    result = analyze(
        "where is the disagreement",
        sdk._get_proj(),
        entity_subgraph_ids={a_id, b_id},
    )
    assert "conflict" in result["answer"].lower(), \
        f"expected conflict in scoped analysis, got: {result['answer']}"


def test_unscoped_falls_back_to_full_graph():
    """Without entity_subgraph_ids, analyze returns full-graph results."""
    db_path = _tmp_db()
    sdk = TortoiseSDK(db_path)
    _seed_small_graph(sdk)

    from tortoise.analyze import analyze

    # Full graph — no entity_subgraph_ids
    result = analyze("where is the disagreement", sdk._get_proj())
    # Should see the disagreement between A and B
    assert "conflict" in result["answer"].lower(), \
        f"expected conflict in full-graph analysis, got: {result['answer']}"


def test_empty_subgraph_returns_no_results():
    """Empty subgraph ID set returns no results for scoped queries."""
    db_path = _tmp_db()
    sdk = TortoiseSDK(db_path)
    _seed_small_graph(sdk)

    from tortoise.analyze import analyze

    # Use "disagreement" — full graph returns conflict, empty subgraph should return None
    result = analyze(
        "where is the disagreement",
        sdk._get_proj(),
        entity_subgraph_ids=set(),
    )
    assert "No conflicts found" in result["answer"], \
        f"expected no conflicts for empty subgraph, got: {result['answer']}"


# ── Runner ──────────────────────────────────────────────────────────

def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall entity-scoped analyze tests passed")


if __name__ == "__main__":
    _run_all()
