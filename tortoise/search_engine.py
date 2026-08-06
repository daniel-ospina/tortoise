"""Hybrid search engine for Tortoise — RRF fusion of FTS + vector + structural queries.

Phase 0 (#7748): Foundation — FalkorDB indexes, RRF fusion, degradation chain,
3-tier query classifier, EP batch annotation, and tortoise_fts_query() API.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict, field
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
    relationships: list[dict] = field(default_factory=list)  # SDK compat (sdk.py passes it; non-point = empty)
    # #125 capture metadata (document entity_type) — optional, empty for non-docs
    topics: list = field(default_factory=list)
    summary: str = ""
    session_id: str = ""
    event_id: str = ""

    def to_dict(self) -> dict:
        """Convert to JSON-safe dict for API responses."""
        d = {
            "id": self.id,
            "content": self.content,
            "point_kind": self.point_kind,
            "context": self.context,
            "match_source": self.match_source,
            "relationships": self.relationships,
            "topics": self.topics,
            "summary": self.summary,
            "sessionId": self.session_id,
            "eventId": self.event_id,
            # Backward-compat aliases (Phase 0 migration from old search() API).
            # IMPORTANT: "similarity" was cosine (0-1) in Phase 0; now it's the
            # RRF fusion score (rank-based, typically 0.01-0.05). Clients that
            # threshold on similarity must recalibrate. (#23)
            "similarity": self.scores.rrf if self.scores else 0.0,
            "snippet": self.content[:200] if len(self.content) > 200 else self.content,
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
    entity_type: 'point' (default), 'event', 'subject', 'document', 'object',
    'source', or 'operator'. Document FTS searches the _searchText index
    (#125) which concatenates title+summary+topics.

    Note: timeout_ms is checked AFTER the query completes (post-hoc).
    A slow query still consumes DB resources — this is a soft guard,
    not a connection-level kill. For connection-level timeout, set it
    at the FalkorDB driver level. (#18)
    """
    if entity_type == "operator":
        # Operators are Points with is_operator=true — match label via CONTAINS
        try:
            start = time.monotonic()
            cypher = (
                "MATCH (n:Point) "
                "WHERE n.is_operator = true AND toLower(n.label) CONTAINS toLower($query) "
                "RETURN n.id, 1.0 AS score "
                "ORDER BY score DESC "
                "LIMIT $limit"
            )
            rows = graph.query(
                cypher, params={"query": query, "limit": limit}
            ).result_set
            elapsed = (time.monotonic() - start) * 1000
            if elapsed > timeout_ms:
                logger.warning("FTS query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
                return []
            return [(row[0], float(row[1])) for row in rows]
        except Exception as e:
            logger.warning("Operator FTS query failed: %s", e)
            return []
    label = entity_type.capitalize()  # point→Point, event→Event, subject→Subject
    try:
        start = time.monotonic()
        cypher = (
            f"CALL db.idx.fulltext.queryNodes('{label}', $query) "
            "YIELD node, score "
            "RETURN node.id, score "
            "ORDER BY score DESC "
            "LIMIT $limit"
        )
        rows = graph.query(
            cypher, params={"query": query, "limit": limit}
        ).result_set
        # Post-hoc timeout check (see docstring for rationale)
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

    Note: timeout_ms is checked AFTER the query completes (post-hoc).
    A slow query still consumes DB resources — this is a soft guard,
    not a connection-level kill. (#18)
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
    entity_type: str = "point", limit: int = 20
) -> list[tuple[str, float]]:
    """Run structural/kind query via range indexes.

    entity_type: 'point' (filters on pointKind/context), 'event' (eventKind),
                 'subject' (subjectKind), 'operator' (op_type, Point nodes with is_operator=true),
                 'source' (sourceKind), 'document' (documentKind), 'object' (objectKind).
    Returns matching entities with a score of 1.0 (exact match) or 0.5 (partial match).
    """
    if entity_type == "operator":
        # Operators are Points with is_operator=true, kind=op_type
        label_str = "Point"
        kind_field = "op_type"
    elif entity_type == "source":
        label_str = "Source"
        kind_field = "sourceKind"
    elif entity_type == "document":
        label_str = "Document"
        kind_field = "documentKind"
    elif entity_type == "object":
        label_str = "Object"
        kind_field = "objectKind"
    else:
        label_str = entity_type.capitalize()
        kind_field = {"point": "pointKind", "event": "eventKind", "subject": "subjectKind"}[entity_type]
    try:
        conditions = []
        params = {}
        if entity_type == "operator":
            conditions.append("n.is_operator = true")
        if kind:
            conditions.append(f"n.{kind_field} = $kind")
            params["kind"] = kind
        if context:
            # Only Point entities and operators have context; skip for Event/Subject
            if entity_type in ("point", "operator"):
                conditions.append("n.context = $context")
                params["context"] = context

        if not conditions:
            return []  # No filters — caller should use full-scan path instead

        where_clause = " AND ".join(conditions)
        cypher = (
            f"MATCH (n:{label_str}) "
            f"WHERE {where_clause} "
            f"RETURN n.id "
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
    expanded_kinds=None,  # accepted for SDK compat; kind expansion is post-retrieval (sdk.py)
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
                run_structural_query, graph, kind, context, entity_type=entity_type, limit=limit
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
        meta = {p["id"]: p for p in points}
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


def get_relationships(graph, point_ids: list[str]) -> dict[str, list[dict]]:
    """Fetch operator-edge relationships for a batch of Point IDs.

    Single Cypher query — NOT N+1. Returns dict mapping point_id → list of
    relationship dicts with {predicate, mechanism, related_id, related_kind,
    related_content, direction, operator_id}.

    Points with no operator edges get an empty list.
    """
    if not point_ids:
        return {}

    rels: dict[str, list[dict]] = {pid: [] for pid in point_ids}

    try:
        cypher = (
            "MATCH (n:Point) WHERE n.id IN $ids "
            "MATCH (n)-[r:IMPL|NAND|hasPart]-(op:Point {is_operator:true}) "
            "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point) "
            "WHERE other.id <> n.id "
            "  AND (other.is_operator IS NULL OR other.is_operator = false) "
            "RETURN n.id, op.op_type AS mechanism, "
            "  coalesce(op.label, '') AS predicate, "
            "  op.id AS operator_id, other.id AS related_id, "
            "  other.pointKind AS related_kind, "
            "  other.content AS related_content, "
            "  r.idx AS n_idx, r2.idx AS other_idx"
        )
        rows = graph.query(cypher, params={"ids": point_ids}).result_set

        for row in rows:
            pid = row[0]
            mechanism = row[1] or "IMPL"
            predicate = row[2]
            operator_id = row[3]
            related_id = row[4]
            related_kind = row[5] or ""
            related_content = row[6] or ""
            n_idx = row[7] if len(row) > 7 else None
            other_idx = row[8] if len(row) > 8 else None

            # Determine direction: idx=0 = source, idx>0 = target.
            # Our point is source → relationship is outgoing.
            # Our point is target + other is source → relationship is incoming.
            if n_idx is not None and n_idx == 0:
                direction = "outgoing"
            else:
                direction = "incoming"

            rel_entry = {
                "predicate": predicate if predicate else "",
                "mechanism": mechanism,
                "related_id": related_id,
                "related_kind": related_kind,
                "related_content": related_content[:200] if related_content else "",
                "direction": direction,
                "operator_id": operator_id,
            }
            if pid in rels:
                rels[pid].append(rel_entry)
    except Exception:
        logger.warning("Relationship query failed", exc_info=True)

    return rels


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
