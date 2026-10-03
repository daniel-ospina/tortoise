"""#3019 — the ranked order must be a function of the DATA, not of DB row order.

Two determinism gaps are pinned here (the issue's parts 1 and 2; part 3 — the leg
SET flipping with wall-clock time via the embedder's negative cache — is a
product decision and is deliberately NOT touched):

  1. **Within-leg tie order.** Every leg ran ``ORDER BY score DESC`` with no
     secondary key, so a tie fell through to DB row order. The operator FTS leg
     is the sharpest case: it returns a CONSTANT ``1.0 AS score`` for every row,
     so the entire leg is one tie *by construction* — no dependence on
     FalkorDBLite's 0.0 fulltext scores.
  2. **A non-finite fusion weight.** ``json.loads`` accepts bare ``NaN`` /
     ``Infinity``, and a NaN weight makes EVERY fused score NaN. Tuple comparison
     against NaN is False in BOTH directions, so the ``(-score, id)`` key that
     #2952 introduced silently degraded to insertion order.

Every test below names (a) the value that makes it FAIL and (b) how the fixture
reaches that value. Tests whose fixture could silently produce nothing carry an
explicit "FIXTURE NOT REACHED" assertion, so a vacuous pass is a failure.

Runnable with:
  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
    uv run pytest tests/test_determinism_tie_order_3019.py -v
"""
from __future__ import annotations

import inspect
import math
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise import search_engine  # noqa: I001
from tortoise.search_engine import (
    reset_circuit_breakers,
    rrf_fusion,
    run_fts_query,
    run_structural_query,
)
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK on a temp database, reclaimed on teardown."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_3019_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Circuit-breaker state is module-level — isolate every test from it."""
    reset_circuit_breakers()
    yield
    reset_circuit_breakers()


# ── part 2: a non-finite fusion weight must not degrade the tie order ───────


def test_non_finite_weight_does_not_degrade_the_tie_order():
    """FAIL VALUE: ``weights={"vector": nan}`` — every fused score becomes NaN, so
    the ``(-score, id)`` comparison is False in both directions and the sort falls
    back to insertion order, yielding ``["b", "a"]``.

    THE FIXTURE REACHES IT: each id occupies the SAME RANK in the two legs
    (``b`` is rank 0 in one and rank 1 in the other; ``a`` the reverse), so the
    two RRF scores are not merely close but EXACTLY equal. The id is then the only
    thing that can order them — which is precisely what a NaN destroys. And ``b``
    is inserted into the accumulator first, so insertion order really is
    ``["b", "a"]``.
    """
    leg_a = [("b", 0.9), ("a", 0.8)]
    leg_b = [("a", 0.9), ("b", 0.8)]

    finite = rrf_fusion([leg_a, leg_b], strategy_names=["vector", "fts"],
                        weights={"vector": 1.0})
    assert list(finite) == ["a", "b"], "the tie-break itself regressed"

    degenerate = rrf_fusion([leg_a, leg_b], strategy_names=["vector", "fts"],
                            weights={"vector": float("nan")})
    assert list(degenerate) == ["a", "b"], (
        "a non-finite weight degraded the fused order to insertion order: "
        f"{list(degenerate)}"
    )


def test_infinite_weight_does_not_destroy_the_score_signal():
    """FAIL VALUE: ``inf`` — every fused score becomes ``inf``, so all candidates
    tie at infinity and the fused SCORES carry no information at all; the ordering
    then rests on the id alone rather than on relevance.

    THE FIXTURE REACHES IT: ``json.loads('{"vector": Infinity}')`` is accepted, so
    an env-supplied weight really can be infinite.

    NOTE ON THE ISSUE'S FRAMING: an infinite weight does not degrade the ORDER
    the way ``NaN`` does — ``inf == inf`` is True, so the tuple falls through to
    the id and stays deterministic. What it costs is the score signal.
    """
    leg_a = [("b", 0.9), ("a", 0.8)]
    leg_b = [("a", 0.9), ("b", 0.8)]
    out = rrf_fusion([leg_a, leg_b], strategy_names=["vector", "fts"],
                     weights={"vector": float("inf")})
    assert list(out) == ["a", "b"], f"infinite weight reordered the result: {list(out)}"
    assert all(math.isfinite(s) for s in out.values()), (
        f"an infinite weight destroyed every fused score: {out}"
    )


# ── part 1: a tied leg must be ordered by id ────────────────────────────────


def test_operator_fts_leg_orders_a_tied_result_by_id(sdk):
    """FAIL VALUE: the operator leg returns its rows in DB row order instead of
    id order. This leg scores EVERY row a constant ``1.0``, so the whole leg is
    one tie and nothing but the secondary key can order it.

    THE FIXTURE REACHES IT: the three nodes are created in DESCENDING id order
    (``zulu``, ``mike``, ``alpha``), so unless id ordering is imposed the result
    cannot come back ``["alpha-op", "mike-op", "zulu-op"]``. This also confirms
    the BRUTE-FORCE path honours multi-key ordering (the operator leg uses no
    fulltext index), which the issue asks to establish.
    """
    graph = sdk._get_proj().g
    for pid in ["zulu-op", "mike-op", "alpha-op"]:
        graph.query(
            "CREATE (n:Point {id:$id, is_operator:true, label:$label})",
            params={"id": pid, "label": "determinism needle"},
        )

    rows = run_fts_query(graph, "determinism needle", entity_type="operator",
                         limit=10, excluded_statuses=())
    got = [pid for pid, _ in rows]

    assert got, "FIXTURE NOT REACHED: the operator leg returned no rows"
    assert len(got) == 3, f"FIXTURE NOT REACHED: expected 3 rows, got {got}"
    assert got == sorted(got), f"a fully-tied leg is not id-ordered: {got}"


# ── part 1, all four sites: the clause must not be tidied away ─────────────


def test_index_fts_leg_orders_a_tied_result_by_id(sdk):
    """FAIL VALUE: the index-accelerated FTS leg returns rows in index order
    instead of id order. #3019 asks to CONFIRM the engine honours multi-key
    ordering on THIS path, not only the brute-force one — so this asserts the
    behaviour rather than the query text.

    THE FIXTURE REACHES IT: the three nodes are created in DESCENDING id order
    and all match the term, so they score identically and the tie is real;
    ``["alpha-ix", "mike-ix", "zulu-ix"]`` is unreachable unless the secondary
    key is applied.
    """
    graph = sdk._get_proj().g
    for pid in ["zulu-ix", "mike-ix", "alpha-ix"]:
        graph.query(
            "CREATE (n:Point {id:$id, content:'determinism needle', "
            "pointKind:'statement'})",
            params={"id": pid},
        )

    rows = run_fts_query(graph, "determinism needle", entity_type="point", limit=10,
                         excluded_statuses=())
    got = [pid for pid, _ in rows]

    assert got, "FIXTURE NOT REACHED: the index FTS leg returned no rows"
    assert len(got) == 3, f"FIXTURE NOT REACHED: expected 3 rows, got {got}"
    assert got == sorted(got), f"an index-path tie is not id-ordered: {got}"


def test_structural_leg_orders_a_constant_scored_result_by_id(sdk):
    """FAIL VALUE: the structural leg returns its rows in DB row order.

    This leg scores EVERY row a CONSTANT (``1.0`` when a kind is given, else
    ``0.5``), so it is one tie exactly like the operator leg — and it carried no
    ``ORDER BY`` at all, which made it the one leg that was completely unordered.

    THE FIXTURE REACHES IT: the three nodes are created in DESCENDING id order,
    so ``["alpha-st", "mike-st", "zulu-st"]`` is unreachable unless the ordering
    is imposed.
    """
    graph = sdk._get_proj().g
    for pid in ["zulu-st", "mike-st", "alpha-st"]:
        graph.query(
            "CREATE (n:Point {id:$id, pointKind:$kind, content:$id})",
            params={"id": pid, "kind": "statement"},
        )

    rows = run_structural_query(graph, "statement", entity_type="point", limit=10,
                                excluded_statuses=())
    got = [pid for pid, _ in rows]

    assert got, "FIXTURE NOT REACHED: the structural leg returned no rows"
    assert len(got) == 3, f"FIXTURE NOT REACHED: expected 3 rows, got {got}"
    assert got == sorted(got), f"a constant-scored leg is not id-ordered: {got}"


def test_every_leg_query_carries_a_secondary_sort_key():
    """A CONTRACT PIN, not behavioural coverage (the behaviour is proved above for
    the three brute-force / index-FTS paths).

    FAIL VALUE: any leg losing its tie key makes the matching assertion fail.
    Asserted clause-by-clause rather than as a COUNT of matches, because a count
    cannot see a NEW leg added later without one — it pins only what it is told
    to pin, and a fixed number silently certifies an omission.

    KNOWN RESIDUAL: the index-accelerated vector path preserves the engine's
    returned order, so two rows with EQUAL distances keep the engine's order and
    rank-based fusion can see a tie-order flip. Fixing it needs the distance from
    signature A (whose score IS its row position) and a deliberate revision of
    two tests that pin this path's order-preservation, so it is tracked as a
    follow-up rather than asserted away by this pin.
    """
    src = inspect.getsource(search_engine)
    assert "ORDER BY score DESC, n.id ASC" in src, "operator leg lost its tie key"
    assert "ORDER BY score DESC, node.{id_field} ASC" in src, "index FTS leg lost its tie key"
    assert "ORDER BY score DESC, n.{id_field} ASC" in src, "vector leg lost its tie key"
    assert "ORDER BY n.{id_field} ASC" in src, "structural leg lost its ordering"
    assert "ORDER BY hops ASC, n.id ASC" in src, "structural-hops leg lost its tie key"
