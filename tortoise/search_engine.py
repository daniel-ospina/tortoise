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

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = EpEvidence()


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
            # Backward-compat aliases (Phase 0 migration from old search() API)
            "similarity": self.scores.rrf if self.scores else 0.0,
            "snippet": self.content[:200] + "..." if len(self.content) > 200 else self.content,
        }
        if self.scores:
            d["scores"] = asdict(self.scores)
        if self.ep and self.ep.evidence is not None:
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
    if not query or not query.strip():
        return {"fts": False, "vector": False, "structural": True}
    return {"fts": True, "vector": True, "structural": True}


# ── FalkorDB query runners ──────────────────────────────────────────────────

def run_fts_query(
    graph, query: str, entity_type: str = "point", limit: int = 20, timeout_ms: int = 500
) -> list[tuple[str, float]]:
    """Run full-text search via FalkorDB FTS index.

    Falls back gracefully if index doesn't exist or query fails.
    entity_type: 'point' (default), 'event', 'subject', or 'document'.
    Returns entity-type-specific identifier: Point/Subject → node.id, Event → node.eventId.
    """
    label = entity_type.capitalize()  # point→Point, event→Event, subject→Subject
    # Event nodes use eventId as their identifier; Point/Subject use id
    id_field = "eventId" if entity_type == "event" else "id"
    try:
        start = time.monotonic()
        cypher = (
            f"CALL db.idx.fulltext.queryNodes('{label}', $query) "
            f"YIELD node, score "
            f"RETURN node.{id_field}, score "
            "ORDER BY score DESC "
            "LIMIT $limit"
        )
        rows = graph.query(
            cypher, params={"query": query, "limit": limit}
        ).result_set
        # NOTE: Timeout is checked AFTER query completes (post-hoc filter).
        # A slow query still consumes DB resources. Future: connection-level timeout.
        # LIMITATION: This is a post-hoc check, not a true query timeout.
        # A query that takes 30s will still run for 30s before being discarded.
        # True connection-level timeout requires FalkorDB client support (not available yet).
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
    graph, query_vec: list[float], limit: int = 20, timeout_ms: int = 500,
    is_embedded: bool = True,
) -> list[tuple[str, float]]:
    """Run vector similarity search via FalkorDB vector index.

    query_vec must be a 384-dim embedding matching the index dimension.

    In Docker/server mode (is_embedded=False), uses the HNSW vector index
    via CALL db.idx.vector.queryNodes. Falls back to brute-force
    vec.euclideanDistance if the index is unavailable (embedded mode,
    old FalkorDB, or index creation failed).
    """
    if not query_vec:
        return []

    # Docker/server mode → try index-accelerated vector search (#7777)
    if not is_embedded:
        try:
            start = time.monotonic()
            cypher = (
                "CALL db.idx.vector.queryNodes('Point', 'embedding', $query_vec, $limit) "
                "YIELD node "
                "RETURN node.id "
                "LIMIT $limit"
            )
            rows = graph.query(
                cypher, params={"query_vec": query_vec, "limit": limit}
            ).result_set
            elapsed = (time.monotonic() - start) * 1000
            if elapsed > timeout_ms:
                logger.warning("Vector query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
                return []
            # Index results are ranked by similarity; assign rank-based scores.
            # RRF fusion uses rank not absolute scores; single-strategy mode
            # gets reasonable descending ordering.
            total = len(rows)
            return [(row[0], 1.0 - (i / max(total, 1))) for i, row in enumerate(rows)]
        except Exception as e:
            msg = str(e).lower()
            if "index" in msg or "not found" in msg or "does not exist" in msg:
                logger.info("Vector index not available — falling back to brute-force")
            else:
                logger.warning("Vector index query failed, falling back to brute-force: %s", e)
            # Fall through to brute-force below

    # Brute-force (embedded mode or index query failed)
    try:
        start = time.monotonic()
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
    graph, kind: str | None, context: str | None,
    entity_type: str = "point", limit: int = 20,
    expanded_kinds: list[str] | None = None,
) -> list[tuple[str, float]]:
    """Run structural/kind query via range indexes.

    entity_type: 'point' (filters on pointKind/context), 'event' (eventKind),
                 'subject' (subjectKind).
    Returns matching entities with a score of 1.0 (exact match) or 0.5 (partial match).
    Event entity_type returns eventId; Point/Subject return id.

    expanded_kinds: pack-expanded kind list for IN-clause filtering.
                   If provided, uses IN clause instead of single `= $kind`.
    """
    label = entity_type.capitalize()
    kind_field = {"point": "pointKind", "event": "eventKind", "subject": "subjectKind"}[entity_type]
    id_field = "eventId" if entity_type == "event" else "id"
    try:
        conditions = []
        params = {}
        if expanded_kinds:
            placeholders = [f"$ekind_{i}" for i in range(len(expanded_kinds))]
            conditions.append(f"n.{kind_field} IN [{', '.join(placeholders)}]")
            for i, k in enumerate(expanded_kinds):
                params[f"ekind_{i}"] = k
        elif kind:
            conditions.append(f"n.{kind_field} = $kind")
            params["kind"] = kind
        if context:
            # Only Point entities have context; skip for Event/Subject
            if entity_type == "point":
                conditions.append("n.context = $context")
                params["context"] = context

        if not conditions:
            return []  # No filters — caller should use full-scan path instead

        where_clause = " AND ".join(conditions)
        cypher = (
            f"MATCH (n:{label}) "
            f"WHERE {where_clause} "
            f"RETURN n.{id_field} "
            f"LIMIT $limit"
        )
        params["limit"] = limit
        rows = graph.query(cypher, params=params).result_set

        # Assign score: 1.0 if both kind AND context match, 0.5 if only one
        results = []
        for row in rows:
            pid = row[0]
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
    entity_type: str = "point",
    limit: int = 20,
    is_embedded: bool = True,
    expanded_kinds: list[str] | None = None,
) -> dict[str, list[tuple[str, float]]]:
    """Run retrieval strategies in parallel with per-strategy degradation.

    Each strategy wraps in try/except → on failure, skip and log.
    Supports partial failure: one strategy down, others continue.
    Strategies run in parallel via ThreadPoolExecutor (P0 fix: #7780).

    is_embedded: True for embedded/redislite mode (brute-force vector),
                 False for Docker/server mode (HNSW index-accelerated).

    Returns:
        {strategy_name: [(id, score), ...]} — only strategies that succeeded
    """
    import concurrent.futures

    results: dict[str, list[tuple[str, float]]] = {}
    futures: dict[concurrent.futures.Future, str] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        # Submit all enabled strategies in parallel
        if strategies.get("fts") and query:
            futures[executor.submit(
                run_fts_query, graph, query, entity_type=entity_type, limit=limit
            )] = "fts"

        if strategies.get("vector") and query_vec:
            futures[executor.submit(
                run_vector_query, graph, query_vec, limit=limit, is_embedded=is_embedded
            )] = "vector"

        if strategies.get("structural"):
            futures[executor.submit(
                run_structural_query, graph, kind, context, entity_type=entity_type, limit=limit,
                expanded_kinds=expanded_kinds,
            )] = "structural"

        # Collect results with 500ms total timeout across all strategies.
        # as_completed(timeout=0.5) raises TimeoutError if any future
        # hasn't completed within 500ms from the start of iteration.
        try:
            for future in concurrent.futures.as_completed(futures, timeout=0.5):
                strategy_name = futures[future]
                strategy_results = future.result()  # already done — no timeout needed
                if strategy_results:
                    results[strategy_name] = strategy_results
                else:
                    logger.info("%s strategy returned no results — degraded", strategy_name)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "Strategies timed out (500ms) — collected %d/%d, cancelling remaining",
                len(results), len(futures),
            )
            for f in futures:
                f.cancel()
        except Exception as e:
            logger.warning("Strategy execution failed: %s — degraded", e)

    return results


# ── EP annotation ────────────────────────────────────────────────────────────

def annotate_ep_batch(graph, point_ids: list[str]) -> dict[str, EpBreakdown]:
    """Fetch EP confidence breakdown for a batch of Point IDs.

    Single Cypher query — NOT N+1. Returns EpBreakdown per Point ID.
    Uses simple edge-count ratio (impl / total) — this is a fast batch annotation,
    not full EP belief propagation. Full EP runs separately via compute_confidence().
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
    except Exception:
        logger.warning("EP batch annotation failed", exc_info=True)
        return {}


# ── TF-IDF fallback (in-memory, from embeddings.py) ─────────────────────────

# ── Relationship / Traversal filters (#7846) ──────────────────────────────────

def filter_by_relationship(
    graph,
    point_ids: list[str],
    predicate: str,
    target_id: str,
    entity_type: str = "point",
    id_field: str = "id",
) -> list[str]:
    """Post-filter: keep only points connected to target_id via operator with label=predicate.

    Operators are middle entities — Product→(op:contains)→Feature = 2 graph hops.
    Traversal: point <-[IMPL]-(op {label:predicate})-[IMPL]-> target.
    Returns filtered list of point IDs (subset of input).
    """
    if not point_ids or not predicate or not target_id:
        return []
    try:
        label = entity_type.capitalize()
        cypher = (
            f"MATCH (n:{label}) WHERE n.{id_field} IN $ids "
            f"MATCH (n)<-[r1:hasPart|IMPL|NAND]-(op:Point {{is_operator:true, label:$pred}})"
            f"-[r2:hasPart|IMPL|NAND]->(t:{label} {{{id_field}: $tid}}) "
            f"RETURN DISTINCT n.{id_field}"
        )
        rows = graph.query(
            cypher,
            params={"ids": point_ids, "pred": predicate, "tid": target_id},
        ).result_set
        return [row[0] for row in rows]
    except Exception:
        logger.warning("Relationship filter failed — returning empty", exc_info=True)
        return []


def filter_by_traversal_predicate(
    graph,
    point_ids: list[str],
    predicate: str,
    entity_type: str = "point",
    id_field: str = "id",
) -> list[str]:
    """Post-filter: keep only points that participate in ANY operator with label=predicate.

    Points are matched if they are either the source or target of an operator
    carrying the given predicate label. Returns filtered list of point IDs.
    """
    if not point_ids or not predicate:
        return []
    try:
        label = entity_type.capitalize()
        cypher = (
            f"MATCH (n:{label}) WHERE n.{id_field} IN $ids "
            f"MATCH (n)<-[r:hasPart|IMPL|NAND]-(op:Point {{is_operator:true, label:$pred}}) "
            f"RETURN DISTINCT n.{id_field}"
        )
        rows = graph.query(
            cypher,
            params={"ids": point_ids, "pred": predicate},
        ).result_set
        return [row[0] for row in rows]
    except Exception:
        logger.warning("Traversal predicate filter failed — returning empty", exc_info=True)
        return []


def fallback_tfidf(query: str, points: list[dict], limit: int = 10) -> list[dict]:
    """Last-resort TF-IDF fallback when all FalkorDB strategies fail.

    points should be dicts with at least 'id', 'content', 'pointKind', 'context'.
    """
    try:
        from tortoise.embeddings import search_points
        meta = {p["id"]: p for p in points if p.get("id")}
        results = search_points(query, points, threshold=0.0, limit=limit)
        return [
            SearchResult(
                id=r["id"],
                content=r["content"],
                point_kind=meta.get(r["id"], {}).get("pointKind", ""),
                context=meta.get(r["id"], {}).get("context"),
                scores=SearchScores(fts=None, vector=None, structural=None, rrf=r["similarity"]),
                match_source="tfidf",
                ep=None,
            ).to_dict()
            for r in results
        ]
    except Exception as e:
        logger.error("TF-IDF fallback failed: %s", e)
        return []
