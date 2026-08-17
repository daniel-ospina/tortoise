"""Retrieval quality metrics (issue #1144 — the loop's quality yardstick).

Graded 3-level relevance (0 = non-relevant, 1 = partially, 2 = relevant)
per the locked eval design (issue comment 2026-08-17):

    - nDCG@10 (headline) — graded, log₂ discount: DCG@10 =
      Σ (2^rel_i − 1) / log₂(i+2); nDCG = DCG / IDCG where IDCG is the
      DCG of the ideal ranking (all grade-2 docs first, then grade-1).
    - P@5 — precision in the top-5, binaryized at grade ≥ 1
      (standard IR practice for graded relevance).
    - R@10 — recall in the top-10 against the oracle denominator
      (the grade-2 target set, per query — "R@10 (oracle denominator)").
    - MRR — reciprocal rank of the first grade-2 (truly relevant) doc.

All functions are pure over (ranked point-id list, {point_id: grade}),
DB-agnostic and unit-testable without a graph. Aggregates are plain means;
uncertainty comes from the paired bootstrap module (bootstrap.py).
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

GRADE_RELEVANT = 2
GRADE_PARTIAL = 1
GRADE_NON = 0

DEFAULT_K_DCG = 10
DEFAULT_K_PRECISION = 5
DEFAULT_K_RECALL = 10

# Metric names used by the report schema.
METRICS = ("ndcg@10", "p@5", "r@10", "mrr")


def dcg_at_k(rels: Sequence[int], k: int = DEFAULT_K_DCG) -> float:
    """DCG@k with the graded log₂ discount: Σ (2^r − 1)/log₂(i+2)."""
    total = 0.0
    for i, rel in enumerate(rels[:k]):
        if rel > 0:
            total += (2.0 ** rel - 1.0) / math.log2(i + 2)
    return total


def ideal_dcg_at_k(
    n_relevant: int, n_partial: int, k: int = DEFAULT_K_DCG,
) -> float:
    """IDCG@k: the ideal ranking puts grade-2 docs first, then grade-1."""
    rels = [GRADE_RELEVANT] * n_relevant + [GRADE_PARTIAL] * n_partial
    return dcg_at_k(rels, k)


def ndcg_at_k(
    ranked_ids: Sequence[str],
    labels: Mapping[str, int],
    k: int = DEFAULT_K_DCG,
) -> float:
    """nDCG@k of `ranked_ids` against graded `labels`.

    Guarded: no grade-2/1 docs in the judged set → 0.0 (nothing to gain).
    IDCG is computed from the ORACLE's relevant counts (n_relevant /
    n_partial), which may exceed k — the ideal top-k is then all grade-2.
    """
    rels = [labels.get(pid, GRADE_NON) for pid in ranked_ids[:k]]
    judged = [r for r in rels if r >= GRADE_PARTIAL]
    if not judged:
        return 0.0
    n_rel = sum(1 for pid, g in labels.items() if g == GRADE_RELEVANT)
    n_part = sum(1 for pid, g in labels.items() if g == GRADE_PARTIAL)
    ideal = ideal_dcg_at_k(n_rel, n_part, k)
    if ideal <= 0.0:
        return 0.0
    return dcg_at_k(rels, k) / ideal


def precision_at_k(
    ranked_ids: Sequence[str],
    labels: Mapping[str, int],
    k: int = DEFAULT_K_PRECISION,
    min_grade: int = GRADE_PARTIAL,
) -> float:
    """P@k: fraction of the top-k with grade ≥ min_grade (binaryized)."""
    if k <= 0:
        return 0.0
    hit = sum(
        1 for pid in ranked_ids[:k] if labels.get(pid, GRADE_NON) >= min_grade
    )
    return hit / k


def recall_at_k(
    ranked_ids: Sequence[str],
    labels: Mapping[str, int],
    k: int = DEFAULT_K_RECALL,
    min_grade: int = GRADE_PARTIAL,
) -> float:
    """R@k against the oracle denominator: |retrieved ∩ relevant| /
    |relevant| where relevant = {pid: grade ≥ min_grade}. Guarded: empty
    relevant set → 0.0."""
    relevant = {
        pid for pid, g in labels.items() if g >= min_grade
    }
    if not relevant:
        return 0.0
    retrieved = {pid for pid in ranked_ids[:k] if labels.get(pid, GRADE_NON) >= min_grade}
    return len(retrieved & relevant) / len(relevant)


def reciprocal_rank(
    ranked_ids: Sequence[str],
    labels: Mapping[str, int],
    min_grade: int = GRADE_RELEVANT,
) -> float:
    """MRR: 1/rank of the first doc with grade ≥ min_grade (default: the
    first TRULY relevant doc). 0.0 when no such doc is in the list."""
    for i, pid in enumerate(ranked_ids, start=1):
        if labels.get(pid, GRADE_NON) >= min_grade:
            return 1.0 / i
    return 0.0


def compute_metrics(
    ranked_ids: Sequence[str],
    labels: Mapping[str, int],
) -> dict[str, float]:
    """All four headline metrics for one ranked list."""
    return {
        "ndcg@10": round(ndcg_at_k(ranked_ids, labels), 6),
        "p@5": round(precision_at_k(ranked_ids, labels), 6),
        "r@10": round(recall_at_k(ranked_ids, labels), 6),
        "mrr": round(reciprocal_rank(ranked_ids, labels), 6),
    }


def aggregate(values: dict[str, list[float]]) -> dict[str, float]:
    """Mean of per-query metric values (per strategy)."""
    out: dict[str, float] = {}
    for name, arr in values.items():
        out[name] = round(sum(arr) / len(arr), 6) if arr else 0.0
    return out
