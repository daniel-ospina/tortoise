"""Hybrid search engine for Tortoise — RRF fusion of FTS + vector + structural queries.

Phase 0 (#7748): Foundation — FalkorDB indexes, RRF fusion, degradation chain,
3-tier query classifier, EP batch annotation, and tortoise_fts_query() API.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict
from typing import Literal

logger = logging.getLogger(__name__)

# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class SearchScores:
    fts: float | None = None
    vector: float | None = None
    structural: float | None = None
    rrf: float = 0.0


@dataclass
class EpEvidence:
    impl_count: int = 0
    nand_count: int = 0
    total: int = 0


@dataclass
class EpBreakdown:
    confidence_mean: float = 0.0
    evidence: EpEvidence | None = None
    contention: float = 0.0


@dataclass
class SearchResult:
    id: str
    content: str
    point_kind: str
    context: str | None = None
    scores: SearchScores | None = None
    match_source: Literal["fts", "vector", "structural", "rrf", "tfidf"] = "rrf"
    ep: EpBreakdown | None = None

    def to_dict(self) -> dict:
        """Convert to JSON-safe dict for API responses."""
        d = {
            "id": self.id,
            "content": self.content,
            "point_kind": self.point_kind,
            "context": self.context,
            "match_source": self.match_source,
        }
        if self.scores:
            d["scores"] = asdict(self.scores)
        if self.ep:
            d["ep"] = asdict(self.ep)
        return d


# ── Query classification ────────────────────────────────────────────────────

def classify_query(
    query: str | None,
    kind: str | None,
    context: str | None,
) -> dict[str, bool]:
    """Determine which retrieval strategies to activate.

    - No text query → structural only (full-scan if context set, kind-filtered otherwise)
    - Text query present → all available strategies (FTS + vector + structural)
    """
    if query is None:
        return {"fts": False, "vector": False, "structural": True}
    return {"fts": True, "vector": True, "structural": True}


# ── FalkorDB query runners ──────────────────────────────────────────────────

def run_fts_query(
    graph, query: str, limit: int = 20, timeout_ms: int = 500
) -> list[tuple[str, float]]:
    """Run full-text search via FalkorDB FTS index.

    Falls back gracefully if index doesn't exist or query fails.
    """
    try:
        start = time.monotonic()
        cypher = (
            "CALL db.idx.fulltext.queryNodes('Point', $query) "
            "YIELD node, score "
            "RETURN node.id, score "
            "ORDER BY score DESC "
            "LIMIT $limit"
        )
        rows = graph.query(
            cypher, params={"query": query, "limit": limit}
        ).result_set
        # NOTE: Timeout is checked AFTER query completes (post-hoc filter).
        # A slow query still consumes DB resources. Future: connection-level timeout.
        elapsed = (time.monotonic() - start) * 1000
        if elapsed > timeout_ms:
            logger.warning("FTS query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
            return []
        return [(row[0], float(row[1])) for row in rows]
    except Exception as e:
        msg = str(e).lower()
        if "index" in msg or "not found" in msg or "does not exist" in msg:
            logger.info("FTS index not available — skipping FTS strategy")
        else:
            logger.warning("FTS query failed: %s", e)
        return []


def run_vector_query(
    graph, query_vec: list[float], limit: int = 20, timeout_ms: int = 500
) -> list[tuple[str, float]]:
    """Run vector similarity search via FalkorDB vector index.

    query_vec must be a 384-dim embedding matching the index dimension.
    """
    if not query_vec:
        return []
    try:
        start = time.monotonic()
        # Use vector similarity search via Cypher
        cypher = (
            "MATCH (n:Point) "
            "WHERE n.embedding IS NOT NULL "
            "WITH n, vec.euclideanDistance(n.embedding, $query_vec) AS distance "
            "WHERE distance IS NOT NULL "
            "RETURN n.id, 1.0 / (1.0 + distance) AS score "
            "ORDER BY score DESC "
            "LIMIT $limit"
        )
        rows = graph.query(
            cypher, params={"query_vec": query_vec, "limit": limit}
        ).result_set
        elapsed = (time.monotonic() - start) * 1000
        if elapsed > timeout_ms:
            logger.warning("Vector query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
            return []
        return [(row[0], float(row[1])) for row in rows]
    except Exception as e:
        msg = str(e).lower()
        if "index" in msg or "not found" in msg or "does not exist" in msg:
            logger.info("Vector index not available — skipping vector strategy")
        elif "embedding" in msg and "null" in msg:
            logger.info("No Points with embeddings — skipping vector strategy")
        else:
            logger.warning("Vector query failed: %s", e)
        return []


def run_structural_query(
    graph, kind: str | None, context: str | None, limit: int = 20
) -> list[tuple[str, float]]:
    """Run structural/kind query via range indexes.

    Returns Points matching kind and/or context with a score of 1.0 (exact match)
    or 0.5 (partial match).
    """
    try:
        conditions = []
        params = {}
        if kind:
            conditions.append("n.pointKind = $kind")
            params["kind"] = kind
        if context:
            conditions.append("n.context = $context")
            params["context"] = context

        if not conditions:
            return []  # No filters — caller should use full-scan path instead

        where_clause = " AND ".join(conditions)
        cypher = (
            f"MATCH (n:Point) "
            f"WHERE {where_clause} "
            f"RETURN n.id, n.content, n.pointKind, n.context "
            f"LIMIT $limit"
        )
        params["limit"] = limit
        rows = graph.query(cypher, params=params).result_set

        # Assign score: 1.0 if both kind AND context match, 0.5 if only one
        results = []
        for row in rows:
            pid, content = row[0], row[1]
            match_score = 1.0 if (kind and context) else 0.5
            results.append((pid, match_score))
        return results
    except Exception as e:
        logger.warning("Structural query failed: %s", e)
        return []


# ── RRF fusion ───────────────────────────────────────────────────────────────

def rrf_fusion(
    ranked_lists: list[list[tuple[str, float]]],
    k: int = 60,
) -> dict[str, float]:
    """Reciprocal Rank Fusion — combine multiple ranked lists.

    Formula: RRF(d) = Σ 1/(k + rank_i(d))

    Args:
        ranked_lists: List of strategies, each is [(id, score), ...] ranked desc
        k: RRF constant (default 60 from Cormack et al. 2009)

    Returns:
        {id: combined_rrf_score} sorted by score descending
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, (pid, _score) in enumerate(ranked):
            rrf_score = 1.0 / (k + rank + 1)
            scores[pid] = scores.get(pid, 0.0) + rrf_score
    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))


# ── Degradation chain ────────────────────────────────────────────────────────

def degradation_chain(
    graph,
    query: str | None,
    kind: str | None,
    context: str | None,
    query_vec: list[float] | None,
    strategies: dict[str, bool],
    limit: int = 20,
) -> dict[str, list[tuple[str, float]]]:
    """Run retrieval strategies with per-strategy degradation.

    Each strategy wraps in try/except → on failure, skip and log.
    Supports partial failure: one strategy down, others continue.

    Returns:
        {strategy_name: [(id, score), ...]} — only strategies that succeeded
    """
    results: dict[str, list[tuple[str, float]]] = {}

    # FTS strategy
    if strategies.get("fts") and query:
        fts_results = run_fts_query(graph, query, limit=limit)
        if fts_results:
            results["fts"] = fts_results
        else:
            logger.info("FTS strategy returned no results — degraded")

    # Vector strategy
    if strategies.get("vector") and query_vec:
        vec_results = run_vector_query(graph, query_vec, limit=limit)
        if vec_results:
            results["vector"] = vec_results
        else:
            logger.info("Vector strategy returned no results — degraded")

    # Structural strategy
    if strategies.get("structural"):
        struct_results = run_structural_query(graph, kind, context, limit=limit)
        if struct_results:
            results["structural"] = struct_results
        else:
            logger.info("Structural strategy returned no results — degraded")

    return results


# ── EP annotation ────────────────────────────────────────────────────────────

def annotate_ep_batch(graph, point_ids: list[str]) -> dict[str, EpBreakdown]:
    """Fetch EP confidence breakdown for a batch of Point IDs.

    Single Cypher query — NOT N+1. Returns EpBreakdown per Point ID.
    Points with no EP data get EpBreakdown with confidence_mean=0.0, contention=0.0.
    """
    if not point_ids:
        return {}

    try:
        cypher = (
            "MATCH (n:Point) "
            "WHERE n.id IN $ids "
            "OPTIONAL MATCH (n)<-[r:IMPL]-(:Point) "
            "WITH n, count(r) AS impl_count "
            "OPTIONAL MATCH (n)<-[r2:NAND]-(:Point) "
            "WITH n, impl_count, count(r2) AS nand_count "
            "RETURN n.id, impl_count, nand_count, "
            "  CASE WHEN impl_count + nand_count > 0 "
            "    THEN toFloat(nand_count) / (impl_count + nand_count) "
            "    ELSE 0.0 "
            "  END AS contention "
        )
        rows = graph.query(cypher, params={"ids": point_ids}).result_set

        breakdowns: dict[str, EpBreakdown] = {}
        for row in rows:
            pid, impl, nand, contention = row[0], int(row[1]), int(row[2]), float(row[3])
            total = impl + nand
            confidence_mean = impl / total if total > 0 else 0.0
            breakdowns[pid] = EpBreakdown(
                confidence_mean=confidence_mean,
                evidence=EpEvidence(impl_count=impl, nand_count=nand, total=total),
                contention=contention,
            )

        # Fill in defaults for IDs with no edges
        for pid in point_ids:
            if pid not in breakdowns:
                breakdowns[pid] = EpBreakdown(
                    confidence_mean=0.0,
                    evidence=EpEvidence(impl_count=0, nand_count=0, total=0),
                    contention=0.0,
                )

        return breakdowns
    except Exception as e:
        logger.warning("EP batch annotation failed: %s", e)
        return {}


# ── TF-IDF fallback (in-memory, from embeddings.py) ─────────────────────────

def fallback_tfidf(query: str, points: list[dict], limit: int = 10) -> list[dict]:
    """Last-resort TF-IDF fallback when all FalkorDB strategies fail.

    points should be dicts with at least 'id', 'content', 'pointKind', 'context'.
    """
    try:
        from tortoise.embeddings import search_points
        # Build lookup for metadata (pointKind, context) from points dicts
        meta = {p["id"]: p for p in points}
        results = search_points(query, points, threshold=0.0, limit=limit)
        return [
            {
                "id": r["id"],
                "content": r["content"],
                "point_kind": meta.get(r["id"], {}).get("pointKind", ""),
                "context": meta.get(r["id"], {}).get("context"),
                "scores": {"fts": None, "vector": None, "structural": None, "rrf": r["similarity"]},
                "match_source": "tfidf",
                "ep": None,
            }
            for r in results
        ]
    except Exception as e:
        logger.error("TF-IDF fallback failed: %s", e)
        return []
