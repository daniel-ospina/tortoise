"""Hybrid search engine for Tortoise — RRF fusion of FTS + vector + structural queries.

Phase 0 (#7748): Foundation — FalkorDB indexes, RRF fusion, degradation chain,
3-tier query classifier, EP batch annotation, and tortoise_fts_query() API.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, asdict, field
from typing import Literal


# ── Circuit breaker (#249) ──────────────────────────────────────────────────
# Per-strategy breaker: trips after consecutive slow/failed queries so a
# wedged FalkorDB (or a pathological query) stops consuming caller time and
# DB resources. While OPEN the strategy short-circuits to degradation
# (empty results → RRF falls through to the next strategy / TF-IDF fallback).
# HALF_OPEN after cooldown lets exactly one probe through; success closes.
# Thread-safe: strategies run concurrently (degradation_chain's executor)
# and search calls share the module-level breakers.


class _CircuitBreaker:
    """Minimal thread-safe circuit breaker for a search strategy.

    State machine: CLOSED → (fail_threshold failures) → OPEN →
    (cooldown_seconds elapsed) → HALF_OPEN → (probe success) → CLOSED
    or (probe failure) → OPEN. At most one HALF_OPEN probe is in flight.
    """

    __slots__ = ("fail_threshold", "cooldown_seconds", "_fails", "_open_until",
                 "_probing", "_lock")

    def __init__(self, fail_threshold: int = 3, cooldown_seconds: float = 30.0):
        self.fail_threshold = fail_threshold
        self.cooldown_seconds = cooldown_seconds
        self._fails = 0
        self._open_until = 0.0  # 0.0 == CLOSED; monotonic deadline while OPEN
        self._probing = False
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """True if a query may run: CLOSED, or the single HALF_OPEN probe."""
        with self._lock:
            if self._open_until == 0.0:
                return True  # CLOSED
            now = time.monotonic()
            if now < self._open_until:
                return False  # OPEN
            # Cooldown expired → HALF_OPEN; exactly one caller probes.
            if self._probing:
                return False
            self._probing = True
            return True

    def is_open(self) -> bool:
        """True if the breaker is currently OPEN (read-only status check)."""
        with self._lock:
            return 0.0 < self._open_until and time.monotonic() < self._open_until

    def record_success(self) -> None:
        with self._lock:
            self._fails = 0
            self._open_until = 0.0
            self._probing = False

    def record_failure(self) -> None:
        with self._lock:
            self._fails += 1
            self._probing = False
            if self._fails >= self.fail_threshold:
                self._open_until = time.monotonic() + self.cooldown_seconds
                logger.warning(
                    "Circuit breaker OPEN for search strategy (%d consecutive failures, "
                    "%.0fs cooldown)",
                    self._fails, self.cooldown_seconds,
                )

    def reset(self) -> None:
        with self._lock:
            self._fails = 0
            self._open_until = 0.0
            self._probing = False


_BREAKERS: dict[str, _CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def _breaker(name: str) -> _CircuitBreaker:
    """Get (creating if needed) the circuit breaker for a strategy."""
    with _BREAKERS_LOCK:
        b = _BREAKERS.get(name)
        if b is None:
            b = _CircuitBreaker()
            _BREAKERS[name] = b
        return b


def reset_circuit_breakers() -> None:
    """Reset all breaker state (used by tests and after DB recovery)."""
    for b in _BREAKERS.values():
        b.reset()


def _breaker_allow(name: str) -> bool:
    """True if the strategy may run (breaker CLOSED or the single HALF_OPEN
    probe)."""
    return _breaker(name).allow()


def _breaker_record(name: str, success: bool) -> None:
    """Record an outcome. Success closes the breaker; failure counts toward
    tripping (a HALF_OPEN probe that fails re-opens immediately)."""
    b = _breaker(name)
    if success:
        b.record_success()
    else:
        b.record_failure()

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
    # Structural ratio nand/(impl+nand) — kept for backward compat. It answers
    # "how much of the incoming evidence is contradiction" but is NOT a
    # posterior-stability measure: a claim with 1 IMPL + 1 NAND that EP
    # converged tightly still reads 0.5.
    contention: float = 0.0
    # True EP posterior variance v = αβ/((α+β)²(α+β+1)) from the persisted
    # ep_alpha/ep_beta — the epistemically correct "is this claim destabilized"
    # signal (same formula as TortoiseEP.get_contested_claims).
    variance: float = 0.0
    # variance > CONTESTED_VARIANCE_THRESHOLD → the claim's posterior is
    # contested: competing evidence is actively destabilizing it. Surface this
    # as a first-class flag so agents treat the claim as disputed, not merely
    # high/low probability.
    contested: bool = False

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = EpEvidence()


@dataclass
class SearchResult:
    id: str
    content: str
    point_kind: str
    scores: SearchScores | None = None
    match_source: Literal["fts", "vector", "structural", "rrf", "tfidf"] = "rrf"
    ep: EpBreakdown | None = None
    relationships: list[dict] = field(default_factory=list)  # SDK compat (sdk.py passes it; non-point = empty)
    # #125 capture metadata (document entity_type) — optional, empty for non-docs
    topics: list = field(default_factory=list)
    summary: str = ""
    session_id: str = ""
    event_id: str = ""
    source_path: str = ""  # #167: file path for agent to open/search

    def to_dict(self) -> dict:
        """Convert to JSON-safe dict for API responses."""
        d = {
            "id": self.id,
            "content": self.content,
            "point_kind": self.point_kind,
            "match_source": self.match_source,
            "relationships": self.relationships,
            "topics": self.topics,
            "summary": self.summary,
            "sessionId": self.session_id,
            "eventId": self.event_id,
            "sourcePath": self.source_path,
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
) -> dict[str, bool]:
    """Determine which retrieval strategies to activate.

    - No text query → structural only (kind-filtered)
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
    Returns n.url for source (canonical key, #448), n.eventId for event,
    n.id for all other entity types.

    Note: the connection-level `timeout` is passed straight to the FalkorDB
    driver (Graph.query(timeout=...)) so a slow query is killed server-side,
    not merely observed. The post-hoc check below remains as a safety net
    for drivers that ignore the timeout. (#249) The per-strategy circuit
    breaker additionally short-circuits after consecutive slow/failed
    queries so a wedged DB stops eating caller latency.
    """
    if entity_type == "operator":
        # Operators are Points with is_operator=true — match label via CONTAINS
        if not _breaker_allow("fts"):
            logger.warning("FTS circuit breaker OPEN — skipping FTS strategy")
            return []
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
                cypher, params={"query": query, "limit": limit}, timeout=timeout_ms
            ).result_set
            elapsed = (time.monotonic() - start) * 1000
            if elapsed > timeout_ms:
                # #561: post-hoc latency warning only — a driver that ignored
                # the timeout returned rows; keep them (real hangs are killed
                # server-side by the driver-level timeout, surfacing as an
                # exception → breaker failure below).
                logger.warning("FTS query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
            _breaker_record("fts", True)
            return [(row[0], float(row[1])) for row in rows]
        except Exception as e:
            logger.warning("Operator FTS query failed: %s", e)
            _breaker_record("fts", False)
            return []
    if not _breaker_allow("fts"):
        logger.warning("FTS circuit breaker OPEN — skipping FTS strategy")
        return []
    label = entity_type.capitalize()  # point→Point, event→Event, subject→Subject
    # #448: three-way id_field — source→url (canonical key, #149),
    # event→eventId, else→id
    if entity_type == "source":
        id_field = "url"
    elif entity_type == "event":
        id_field = "eventId"
    else:
        id_field = "id"
    try:
        start = time.monotonic()
        cypher = (
            f"CALL db.idx.fulltext.queryNodes('{label}', $query) "
            "YIELD node, score "
            f"RETURN node.{id_field}, score "
            "ORDER BY score DESC "
            "LIMIT $limit"
        )
        rows = graph.query(
            cypher, params={"query": query, "limit": limit}, timeout=timeout_ms
        ).result_set
        # Post-hoc timeout check (see docstring for rationale) — #561: log
        # latency, return the results (the driver-level timeout is the real
        # hang-killer; results received = normal completion for the breaker).
        elapsed = (time.monotonic() - start) * 1000
        if elapsed > timeout_ms:
            logger.warning("FTS query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
        _breaker_record("fts", True)
        return [(row[0], float(row[1])) for row in rows]
    except Exception as e:
        msg = str(e).lower()
        if "index" in msg or "not found" in msg or "does not exist" in msg:
            # Benign — expected in embedded FalkorDBLite (no FTS index) and for
            # labels without an index. Degrades quietly; does NOT count toward
            # tripping the breaker (a healthy deployment with one index-less
            # label must not disable FTS for all labels). (#249 review P1-1)
            logger.info("FTS index not available — skipping FTS strategy")
            _breaker_record("fts", True)
        else:
            logger.warning("FTS query failed: %s", e)
            _breaker_record("fts", False)
        return []


def run_vector_query(
    graph, query_vec: list[float], limit: int = 20, timeout_ms: int = 500,
    is_embedded: bool = True, entity_type: str = "point",
) -> list[tuple[str, float]]:
    """Run vector similarity search via FalkorDB vector index.

    query_vec must be a 384-dim embedding matching the index dimension.

    In Docker/server mode (is_embedded=False), uses the HNSW vector index
    via CALL db.idx.vector.queryNodes. Falls back to brute-force
    vec.euclideanDistance if the index is unavailable (embedded mode,
    old FalkorDB, or index creation failed).

    entity_type: 'point' (default), 'event', 'subject', 'document', 'object',
    'source', or 'operator'. The vector index is queried against the label
    matching the entity_type (Event/Subject/Document/Object/...), and results
    return the entity's id field: url for source (canonical key, #448),
    eventId for event, id for all other entity types.
    Operators are Points with is_operator=true — they query the Point label.
    (#172)

    Note: the connection-level `timeout` is passed straight to the FalkorDB
    driver (Graph.query(timeout=...)) so a slow query is killed server-side.
    The post-hoc check remains as a safety net for drivers that ignore the
    timeout, and the per-strategy circuit breaker short-circuits after
    consecutive slow/failed queries. (#249)
    """
    if not query_vec:
        return []
    if not _breaker_allow("vector"):
        logger.warning("Vector circuit breaker OPEN — skipping vector strategy")
        return []

    # Operators are Points with is_operator=true — match the Point label
    # (consistent with run_fts_query / run_structural_query). (#172)
    label = "Point" if entity_type == "operator" else entity_type.capitalize()
    # #448: three-way id_field — source→url (canonical key, #149),
    # event→eventId, else→id
    if entity_type == "source":
        id_field = "url"
    elif entity_type == "event":
        id_field = "eventId"
    else:
        id_field = "id"

    # Docker/server mode → try index-accelerated vector search (#7777)
    if not is_embedded:
        try:
            start = time.monotonic()
            cypher = (
                f"CALL db.idx.vector.queryNodes('{label}', 'embedding', $query_vec, $limit) "
                "YIELD node "
                f"RETURN node.{id_field} "
                "LIMIT $limit"
            )
            rows = graph.query(
                cypher, params={"query_vec": query_vec, "limit": limit}, timeout=timeout_ms
            ).result_set
            elapsed = (time.monotonic() - start) * 1000
            if elapsed > timeout_ms:
                # #561: latency warning only — keep the rows.
                logger.warning("Vector query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
            _breaker_record("vector", True)
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
            # Fall through to brute-force below — the OUTCOME is recorded by
            # whichever path finishes (success or the brute-force except), so
            # one logical query counts at most once. (#249 review P1-2)

    # Brute-force (embedded mode or index query failed)
    try:
        start = time.monotonic()
        # #244: vec.euclideanDistance rejects plain-list query params
        # ("expected Null or Vectorf32 but was List") in embedded FalkorDBLite
        # and whenever the HNSW index is unavailable — wrap the param in
        # vecf32() so vector search actually runs. Stored embeddings must be
        # vecf32-encoded too (a single plain-list node poisons the whole
        # MATCH — see _upsert_event / session indexers).
        cypher = (
            f"MATCH (n:{label}) "
            "WHERE n.embedding IS NOT NULL "
            "WITH vecf32($query_vec) AS _qv, n "
            "WITH n, vec.euclideanDistance(n.embedding, _qv) AS distance "
            "WHERE distance IS NOT NULL "
            f"RETURN n.{id_field}, 1.0 / (1.0 + distance) AS score "
            "ORDER BY score DESC "
            "LIMIT $limit"
        )
        rows = graph.query(
            cypher, params={"query_vec": query_vec, "limit": limit}, timeout=timeout_ms
        ).result_set
        elapsed = (time.monotonic() - start) * 1000
        if elapsed > timeout_ms:
            # #561: latency warning only — return the results.
            logger.warning("Vector query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
        _breaker_record("vector", True)
        return [(row[0], float(row[1])) for row in rows]
    except Exception as e:
        msg = str(e).lower()
        if "index" in msg or "not found" in msg or "does not exist" in msg:
            logger.info("Vector index not available — skipping vector strategy")
            _breaker_record("vector", True)
        elif "embedding" in msg and "null" in msg:
            logger.info("No Points with embeddings — skipping vector strategy")
            _breaker_record("vector", True)
        else:
            logger.warning("Vector query failed: %s", e)
            _breaker_record("vector", False)
        return []


def run_structural_query(
    graph, kind: str | None,
    entity_type: str = "point", limit: int = 20, timeout_ms: int = 500
) -> list[tuple[str, float]]:
    """Run structural/kind query via range indexes.

    entity_type: 'point' (filters on pointKind), 'event' (eventKind),
                 'subject' (subjectKind), 'operator' (op_type, Point nodes with is_operator=true),
                 'source' (sourceKind), 'document' (documentKind), 'object' (objectKind).
    Returns matching entities with a score of 1.0 (exact match) or 0.5 (partial match).

    #249: the driver-level timeout is passed through so a wedged DB cannot
    hang the structural strategy (the third leg of the degradation chain);
    the per-strategy breaker short-circuits after consecutive failures.
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
    if entity_type == "source":
        id_field = "url"  # #149: Source canonical key is url, not id
    elif entity_type == "event":
        id_field = "eventId"
    else:
        id_field = "id"
    if not _breaker_allow("structural"):
        logger.warning("Structural circuit breaker OPEN — skipping structural strategy")
        return []
    try:
        conditions = []
        params = {}
        if entity_type == "operator":
            conditions.append("n.is_operator = true")
        if kind:
            conditions.append(f"n.{kind_field} = $kind")
            params["kind"] = kind

        if not conditions:
            # No filters — caller should use full-scan path instead. Normal
            # completion: record success so a HALF_OPEN probe never latches.
            _breaker_record("structural", True)
            return []

        where_clause = " AND ".join(conditions)
        cypher = (
            f"MATCH (n:{label_str}) "
            f"WHERE {where_clause} "
            f"RETURN n.{id_field} "
            f"LIMIT $limit"
        )
        params["limit"] = limit
        rows = graph.query(cypher, params=params, timeout=timeout_ms).result_set

        # Score: 1.0 if kind matched, 0.5 for no-kind broad scan
        results = []
        for row in rows:
            pid = row[0]
            match_score = 1.0 if kind else 0.5
            results.append((pid, match_score))
        _breaker_record("structural", True)
        return results
    except Exception as e:
        msg = str(e).lower()
        if "index" in msg or "not found" in msg or "does not exist" in msg:
            logger.info("Structural index not available — skipping structural strategy")
            _breaker_record("structural", True)
        else:
            logger.warning("Structural query failed: %s", e)
            _breaker_record("structural", False)
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
    entity_type: 'point' (default), 'event', 'subject', 'document', 'object',
                 'source', or 'operator' — forwarded to each retrieval
                 strategy to query the correct entity label/id field. (#172)

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
                run_vector_query, graph, query_vec, limit=limit, is_embedded=is_embedded,
                entity_type=entity_type,
            )] = "vector"

        if strategies.get("structural"):
            futures[executor.submit(
                run_structural_query, graph, kind, entity_type=entity_type, limit=limit
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

# Variance threshold above which a claim's posterior is considered contested.
# Must match TortoiseEP.get_contested_claims (tortoise/ep.py).
CONTESTED_VARIANCE_THRESHOLD = 0.04


def _beta_variance(alpha: float, beta: float) -> float:
    """Variance of the Beta(α, β) posterior: αβ/((α+β)²(α+β+1)).

    Max is 1/12 ≈ 0.0833 at α=β=1 (uninformative prior). Same formula as
    TortoiseEP.get_contested_claims.
    """
    s = alpha + beta
    if s <= 0:
        return 0.0
    return (alpha * beta) / (s * s * (s + 1))


def annotate_ep_batch(graph, point_ids: list[str]) -> dict[str, EpBreakdown]:
    """Fetch EP confidence breakdown for a batch of Point IDs.

    Single Cypher query — NOT N+1. Returns EpBreakdown per Point ID.
    Points with no EP data get EpBreakdown with confidence_mean=0.0, contention=0.0.
    variance/contested are computed from the PERSISTED ep_alpha/ep_beta
    (posterior stability), not from edge ratios.
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
            "  END AS contention, "
            "  coalesce(n.ep_alpha, 1.0) AS alpha, coalesce(n.ep_beta, 1.0) AS beta, "
            "  n.ep_alpha IS NOT NULL AS has_ep "
        )
        rows = graph.query(cypher, params={"ids": point_ids}).result_set

        breakdowns: dict[str, EpBreakdown] = {}
        for row in rows:
            pid, impl, nand, contention, alpha, beta, has_ep = row[0], int(row[1]), int(row[2]), float(row[3]), float(row[4]), float(row[5]), row[6]
            total = impl + nand
            confidence_mean = impl / total if total > 0 else 0.0
            variance = _beta_variance(alpha, beta)
            breakdowns[pid] = EpBreakdown(
                confidence_mean=confidence_mean,
                evidence=EpEvidence(impl_count=impl, nand_count=nand, total=total),
                contention=contention,
                variance=round(variance, 6),
                # Contested only when EP actually ran: an uncalibrated point
                # (no persisted α/β → defaults to 1/1 → v=1/12) is NOT
                # contested, it's unmeasured.
                contested=bool(has_ep) and variance > CONTESTED_VARIANCE_THRESHOLD,
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
    # Relationship filters only make sense for Point-backed entities — the chain
    # always traverses operator edges, which only connect Points. Bail early
    # with a warning instead of silently returning empty for unsupported types.
    if entity_type not in ("point", "operator"):
        logger.warning("Relationship filters not supported for entity_type=%s", entity_type)
        return []
    try:
        # Operators are Points with is_operator=true — no Operator label exists (#148).
        # Map both point and operator to the Point label, mirroring
        # run_fts_query / run_structural_query label logic.
        label = "Point"
        is_operator_clause = " AND n.is_operator = true" if entity_type == "operator" else ""
        cypher = (
            f"MATCH (n:{label}) WHERE n.{id_field} IN $ids{is_operator_clause} "
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
    # Traversal predicate filters only make sense for Point-backed entities —
    # the chain always traverses operator edges, which only connect Points.
    # Bail early with a warning instead of silently returning empty for
    # unsupported types.
    if entity_type not in ("point", "operator"):
        logger.warning(
            "Traversal predicate filters not supported for entity_type=%s", entity_type
        )
        return []
    try:
        # Operators are Points with is_operator=true — no Operator label exists (#148).
        # Map both point and operator to the Point label, mirroring
        # run_fts_query / run_structural_query label logic.
        label = "Point"
        is_operator_clause = " AND n.is_operator = true" if entity_type == "operator" else ""
        cypher = (
            f"MATCH (n:{label}) WHERE n.{id_field} IN $ids{is_operator_clause} "
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

    points should be dicts with at least 'id', 'content', 'pointKind'.
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
                scores=SearchScores(fts=None, vector=None, structural=None, rrf=r["similarity"]),
                match_source="tfidf",
                ep=None,
            ).to_dict()
            for r in results
        ]
    except Exception as e:
        logger.error("TF-IDF fallback failed: %s", e)
        return []
