"""Unit tests for the #1144 retrieval metrics math (pure, no graph)."""
from __future__ import annotations

import math

import pytest

from tests.eval.retrieval.metrics import (
    GRADE_NON,
    GRADE_PARTIAL,
    GRADE_RELEVANT,
    aggregate,
    compute_metrics,
    dcg_at_k,
    ideal_dcg_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)


# ── DCG / nDCG ──────────────────────────────────────────────────────────────

def _expected_dcg(rels):
    return sum((2.0 ** r - 1.0) / math.log2(i + 2) for i, r in enumerate(rels))


def test_dcg_hand_computed():
    rels = [GRADE_RELEVANT] * 10  # 2,2,2,2,2,2,2,2,2,2
    expected = sum(3.0 / math.log2(i + 2) for i in range(10))
    assert math.isclose(dcg_at_k(rels, 10), expected, rel_tol=1e-9)
    assert math.isclose(dcg_at_k(rels, 10), _expected_dcg(rels), rel_tol=1e-9)


def test_dcg_graded_log2_discount():
    """Partial (1) contributes 1/log2, relevant (2) contributes 3/log2 —
    the graded log2 discount is the spec's formula."""
    assert math.isclose(
        dcg_at_k([GRADE_PARTIAL], 10), 1.0, rel_tol=1e-9
    )
    assert math.isclose(
        dcg_at_k([GRADE_RELEVANT], 10), 3.0, rel_tol=1e-9
    )
    assert math.isclose(
        dcg_at_k([GRADE_NON], 10), 0.0, rel_tol=1e-9
    )


def test_ndcg_perfect_and_worst():
    ids = [f"p{i}" for i in range(20)]
    labels = {f"p{i}": GRADE_RELEVANT for i in range(10)}
    # Perfect ranking → nDCG 1.0 (IDCG = DCG).
    assert math.isclose(ndcg_at_k(ids, labels, 10), 1.0, rel_tol=1e-9)
    # Bury the relevants under grade-0 docs → strictly below 1.0.
    buried = [f"p{i}" for i in range(10, 20)] + ids[:10]
    reversed_ = ndcg_at_k(buried, labels, 10)
    assert 0.0 <= reversed_ < 1.0


def test_ndcg_idcg_capped_by_corpus_relevants():
    """IDCG uses the ORACLE's grade-2 count — when the corpus has fewer
    relevant docs than k, IDCG is shorter than k terms."""
    labels = {f"p{i}": GRADE_RELEVANT for i in range(5)}  # only 5 relevant
    ids = [f"p{i}" for i in range(5)] + [f"x{i}" for i in range(5)]
    assert math.isclose(ndcg_at_k(ids, labels, 10), 1.0, rel_tol=1e-9)
    # A ranking that buries the relevants scores strictly less.
    buried = [f"x{i}" for i in range(5)] + [f"p{i}" for i in range(5)]
    assert ndcg_at_k(buried, labels, 10) < 1.0


def test_ndcg_empty_judged_returns_zero():
    labels = {f"p{i}": GRADE_NON for i in range(10)}
    assert ndcg_at_k([f"p{i}" for i in range(10)], labels, 10) == 0.0


def test_ideal_dcg():
    # 3 relevant + 2 partial: ideal ranking = 2,2,2,1,1 (then k truncates).
    expected = _expected_dcg([2, 2, 2, 1, 1, 0, 0, 0, 0, 0])
    assert math.isclose(ideal_dcg_at_k(3, 2, 10), expected, rel_tol=1e-9)


# ── P@5 / R@10 / MRR ────────────────────────────────────────────────────────

def test_precision_at_k_binaryized():
    ids = ["a", "b", "c", "d", "e"]
    labels = {"a": 2, "b": 1, "c": 0, "d": 2, "e": 0}
    # top-5: grade>=1 on a,b,d → 3/5
    assert math.isclose(precision_at_k(ids, labels, 5), 0.6)
    # top-3: a,b → 2/3
    assert math.isclose(precision_at_k(ids, labels, 3), 2 / 3)
    # strict (grade>=2 only): a,d → 2/5
    assert math.isclose(precision_at_k(ids, labels, 5, min_grade=2), 0.4)


def test_recall_at_k_oracle_denominator():
    labels = {f"p{i}": 2 for i in range(20)} | {f"q{i}": 1 for i in range(10)}
    # Binaryized at grade>=1 (the standard recall convention): the oracle
    # relevant set is the 30 grade-2 + grade-1 points.
    ids = [f"p{i}" for i in range(10)]
    assert math.isclose(recall_at_k(ids, labels, 10), 10 / 30, rel_tol=1e-9)
    # none retrieved → 0
    assert recall_at_k(["zz"] * 10, labels, 10) == 0.0
    # empty relevant set → 0 (guarded)
    assert recall_at_k(["a"] * 10, {"a": 0}, 10) == 0.0


def test_reciprocal_rank_first_relevant():
    labels = {"p0": 2, "p3": 1, "p5": 2}
    assert math.isclose(reciprocal_rank(["x", "p3", "p0"], labels), 1 / 3)
    assert math.isclose(reciprocal_rank(["p5", "x", "p0"], labels), 1.0)
    assert reciprocal_rank(["x", "y"], labels) == 0.0
    # MRR defaults to the first grade-2 (partial at rank 1 does not count)
    assert math.isclose(reciprocal_rank(["p3", "p0"], labels), 0.5)


def test_compute_metrics_shape():
    ids = ["p0", "p1", "p2", "p3", "p4"]
    labels = {"p0": 2, "p1": 1, "p2": 2, "p3": 0, "p4": 2}
    m = compute_metrics(ids, labels)
    assert set(m) == {"ndcg@10", "p@5", "r@10", "mrr"}
    assert 0.0 <= m["ndcg@10"] <= 1.0
    assert m["p@5"] == pytest.approx(0.8)
    assert m["mrr"] == pytest.approx(1.0)


def test_aggregate_mean():
    agg = aggregate({"ndcg@10": [0.5, 1.0], "p@5": [0.0, 1.0]})
    assert agg["ndcg@10"] == pytest.approx(0.75)
    assert agg["p@5"] == pytest.approx(0.5)
    assert aggregate({"ndcg@10": []})["ndcg@10"] == 0.0
