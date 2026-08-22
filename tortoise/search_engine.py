"""Hybrid search engine for Tortoise — RRF fusion of FTS + vector + structural queries.

Phase 0 (#7748): Foundation — FalkorDB indexes, RRF fusion, degradation chain,
3-tier query classifier, EP batch annotation, and tortoise_fts_query() API.
"""
from __future__ import annotations  # noqa: I001

import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, asdict, field
from typing import Literal

# #1391: terminal (no-longer-current) Point statuses EXCLUDED from every
# default read surface (FTS/vector/structural/operator + sdk query paths).
# #689 excluded only 'retracted'; supersede_point writes 'superseded' +
# 'outdated' (terminal per ONTOLOGY §512), 'deprecated' is used in practice
# (recall_state STATE_EXCLUDED_STATUS, EP criteria) and 'archived' is the
# same family — none must be served as current. Audit/history queries opt in
# via include_terminal (tortoise_fts_query), sdk.query/paginated_query
# (include_retracted=True) or an explicit status= filter.
TERMINAL_EXCLUDED_STATUSES = (
    "retracted", "superseded", "outdated", "archived", "deprecated")


def _exclude_status_clause(alias: str,
                           excluded: tuple[str, ...] = TERMINAL_EXCLUDED_STATUSES) -> str:
    """Cypher WHERE fragment excluding the terminal statuses on ``alias``
    (<>-chain form — FalkorDB rejects inline string lists in ``IN``), plus
    the ``outdated=true`` legacy flag (invalidate_point writes the flag
    without touching status). ``excluded=()`` produces the empty string
    (no exclusion — the audit/full-scan opt-in)."""
    if not excluded:
        return ""
    chain = " AND ".join(f"{alias}.status <> '{s}'" for s in excluded)
    return (f"(({alias}.status IS NULL OR ({chain})) "
            f"AND coalesce({alias}.outdated, false) = false)")


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

    __slots__ = ("fail_threshold", "cooldown_seconds", "_fails", "_open_until",  # noqa: RUF023
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
            return 0.0 < self._open_until and time.monotonic() < self._open_until  # noqa: SIM300

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
    # Whether this point has persisted EP data (ep_alpha / ep_beta).
    # False means the point is uncalibrated — Beta(1,1) default priors
    # produce variance 0.0833 but that is NOT a signal of contestation.
    has_ep: bool = False

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
    # #1353 promoted epistemic state (D8) — first-class, absent when unknown:
    status: str = ""  # live / superseded / deprecated / retracted / draft
    superseded_by: dict | None = None  # {id, content_snippet, created_at} | None
    supersedes: list[dict] = field(default_factory=list)  # [{id, content_snippet, created_at}]
    subject: dict | None = None  # {id, name, kind} | None — ≤1 hop, fail-closed (D10)

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
        # #1353 promoted state — additive keys, emitted only when known (#1353 D8)
        if self.status:
            d["status"] = self.status
        if self.superseded_by:
            d["superseded_by"] = self.superseded_by
        if self.supersedes:
            d["supersedes"] = self.supersedes
        if self.subject:
            d["subject"] = self.subject
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

#: R3 (#1542) D4 leg-trace entry shape (the R2 #1541 shared contract):
#: {"leg", "ran", "degraded", "reason", "count"}. ``reason`` is never null
#: when ``degraded`` is true (shape rule).
def _trace_entry(leg: str, *, ran: bool, degraded: bool,
                 reason: str | None, count: int) -> dict:
    return {"leg": leg, "ran": ran, "degraded": degraded,
            "reason": reason, "count": count}


def run_fts_query(
    graph, query: str, entity_type: str = "point", limit: int = 20,
    timeout_ms: int = 500, excluded_statuses: tuple | None = None,
    leg_trace: list[dict] | None = None,
) -> list[tuple[str, float]]:
    """Run full-text search via FalkorDB FTS index.

    Falls back gracefully if index doesn't exist or query fails.
    entity_type: 'point' (default), 'event', 'subject', 'document', 'object',
    'source', or 'operator'. Document FTS searches the _searchText index
    (#125) which concatenates title+summary+topics.
    Returns n.url for source (canonical key, #448), n.eventId for event,
    n.id for all other entity types.

    R3 (#1542) D4: ``leg_trace`` — when provided, appends a per-leg entry
    at EVERY exit branch (the FTS leg is recorded AT THE SOURCE, never
    reconstructed from the results dict — an absent strategy is
    indistinguishable from one that ran-and-returned-empty). The index-
    missing catch is promoted to ``reason="index_missing"`` so surface 11's
    owned negative ("fts skipped (no index)" vs "fts ran, no results") is
    truthful from R3 forward. Default None = no trace (byte-identical).

    R2 (#1541) D1: the ``queryNodes`` path runs ``build_or_query`` — the
    shared OR-union tokenizer (tortoise.sparse) replaces the strict-AND raw
    passthrough so a point differing from the question by ONE token is no
    longer zeroed (RediSearch AND requires every whitespace term present).
    The operator path (label CONTAINS) is structural and unchanged; full-
    scan mode (query=None) never reaches this function. Single-token and
    stopword-only queries return the raw string — byte-identical to the
    pre-R2 behavior.

    Note: the connection-level `timeout` is passed straight to the FalkorDB
    driver (Graph.query(timeout=...)) so a slow query is killed server-side,
    not merely observed. The post-hoc check below remains as a safety net
    for drivers that ignore the timeout. (#249) The per-strategy circuit
    breaker additionally short-circuits after consecutive slow/failed
    queries so a wedged DB stops eating caller latency.
    """
    def _record(*, ran: bool, degraded: bool, reason: str | None,
                count: int) -> None:
        if leg_trace is not None:
            leg_trace.append(_trace_entry("fts", ran=ran, degraded=degraded,
                                          reason=reason, count=count))

    if entity_type == "operator":
        # Operators are Points with is_operator=true — match label via CONTAINS
        if not _breaker_allow("fts"):
            logger.warning("FTS circuit breaker OPEN — skipping FTS strategy")
            _record(ran=False, degraded=True, reason="breaker_open", count=0)
            return []
        try:
            start = time.monotonic()
            cypher = (
                "MATCH (n:Point) "
                "WHERE n.is_operator = true"
                + ("" if excluded_statuses == ()
                   else f" AND {_exclude_status_clause('n', excluded_statuses or TERMINAL_EXCLUDED_STATUSES)}")
                + "  AND toLower(n.label) CONTAINS toLower($query) "
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
            _record(ran=True, degraded=False, reason="ok", count=len(rows))
            return [(row[0], float(row[1])) for row in rows]
        except Exception as e:
            logger.warning("Operator FTS query failed: %s", e)
            _breaker_record("fts", False)
            _record(ran=True, degraded=True, reason="query_failed", count=0)
            return []
    if not _breaker_allow("fts"):
        logger.warning("FTS circuit breaker OPEN — skipping FTS strategy")
        _record(ran=False, degraded=True, reason="breaker_open", count=0)
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
    # R2 (#1541) D1: OR-union tolerance for the text path — all labels
    # share the tokenizer (single-token/degenerate inputs pass the raw
    # string through unchanged).
    from .sparse import build_or_query
    fts_query = build_or_query(query)
    # #689/#1391: terminal-status Points must not leak into FTS results
    # (skipped when the caller opts in via include_terminal — audit/history).
    if label == "Point":
        status_filter = ("" if excluded_statuses == ()
                         else f"WHERE {_exclude_status_clause('node', excluded_statuses or TERMINAL_EXCLUDED_STATUSES)} ")
    else:
        status_filter = ""
    try:
        start = time.monotonic()
        cypher = (
            f"CALL db.idx.fulltext.queryNodes('{label}', $query) "
            "YIELD node, score "
            + status_filter +
            f"RETURN node.{id_field}, score "
            "ORDER BY score DESC "
            "LIMIT $limit"
        )
        rows = graph.query(
            cypher, params={"query": fts_query, "limit": limit}, timeout=timeout_ms
        ).result_set
        # Post-hoc timeout check (see docstring for rationale) — #561: log
        # latency, return the results (the driver-level timeout is the real
        # hang-killer; results received = normal completion for the breaker).
        elapsed = (time.monotonic() - start) * 1000
        if elapsed > timeout_ms:
            logger.warning("FTS query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
        _breaker_record("fts", True)
        # R3 (#1542) D4: a clean zero-hit run is ``empty_results`` (the leg
        # RAN and found nothing) — distinct from ``index_missing`` below.
        _record(ran=True, degraded=False,
                reason=("ok" if rows else "empty_results"), count=len(rows))
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
            # R3 (#1542) D4: the promoted index-missing trace (surface 11's
            # owned negative — "fts skipped (no index)" is recorded, never
            # silently conflated with "fts ran, no results").
            _record(ran=True, degraded=True, reason="index_missing", count=0)
        else:
            logger.warning("FTS query failed: %s", e)
            _breaker_record("fts", False)
            _record(ran=True, degraded=True, reason="query_failed", count=0)
        return []


def run_vector_query(
    graph, query_vec: list[float], limit: int = 20, timeout_ms: int = 500,
    is_embedded: bool = True, entity_type: str = "point",
    vector_index_api: str | None = None, excluded_statuses: tuple | None = None,
    leg_trace: list[dict] | None = None,
) -> list[tuple[str, float]]:
    """Run vector similarity search via FalkorDB vector index.

    query_vec must be a 384-dim embedding matching the index dimension.

    R3 (#1542) D4: ``leg_trace`` — when provided, appends a per-leg entry
    at every exit branch. The empty-embedding case is an EXPLICIT zero-row
    guard on the success path (an all-no-embedding graph returns [] WITHOUT
    raising — the exception-only catch never fires for the real empty case):
    {0-row result set with zero embedded points → ``no_embeddings``},
    {0-row result set with embedded points present → ``empty_results``},
    {malformed plain-list embedding node (the vecf32 MATCH throws per-row)
    or driver exception → ``query_failed``}. Default None = no trace.

    In Docker/server mode (is_embedded=False), uses the HNSW vector index
    via CALL db.idx.vector.queryNodes — with both known argument orders
    (#1359): signature A (label, attr, vec, k) for the RediSearch-style
    repo-pinned docker image, signature B (label, attr, k, vecf32(vec))
    for Cypher-native engines (falkordblite 0.10.0's bundled module).
    Falls back to brute-force vec.euclideanDistance if the index is
    unavailable (embedded mode, old FalkorDB, or index creation failed).

    vector_index_api: 'procedure' | 'cypher' | None — the API that
    succeeded at index-creation time, recorded on the projection as
    FalkorProjection._vector_index_api (#1359). When 'cypher', the
    signature-B form is attempted FIRST (an engine that created the index
    via `CREATE VECTOR INDEX` rejects signature A — skipping it saves a
    failed round trip per query). 'procedure' or None keeps the historical
    behavior: signature A first, retry B on signature failure. Brute-force
    remains the fallback in all cases.

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
    def _record(*, ran: bool, degraded: bool, reason: str | None,
                count: int) -> None:
        if leg_trace is not None:
            leg_trace.append(_trace_entry("vector", ran=ran,
                                          degraded=degraded,
                                          reason=reason, count=count))

    if not query_vec:
        return []
    if not _breaker_allow("vector"):
        logger.warning("Vector circuit breaker OPEN — skipping vector strategy")
        _record(ran=False, degraded=True, reason="breaker_open", count=0)
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
        # #689: retracted Points must not leak into vector results.
        if label == "Point":
            vec_status_filter = ("" if excluded_statuses == ()
                                 else f"WHERE {_exclude_status_clause('node', excluded_statuses or TERMINAL_EXCLUDED_STATUSES)} ")
        else:
            vec_status_filter = ""
        try:
            start = time.monotonic()

            def _sig_cypher(sig: str) -> str:
                if sig == "B":
                    # Docs form: k first, vecf32-wrapped query — verified on
                    # falkordblite 0.10.0's bundled module ("Invalid arguments
                    # for procedure" on A, works on B).
                    return (
                        f"CALL db.idx.vector.queryNodes('{label}', 'embedding', $limit, vecf32($query_vec)) "
                        "YIELD node, score "
                        + vec_status_filter +
                        f"RETURN node.{id_field}, score "
                        "LIMIT $limit"
                    )
                # Signature A (RediSearch-style): repo-pinned docker image
                # falkordb/falkordb-server:v4.16.7.
                return (
                    f"CALL db.idx.vector.queryNodes('{label}', 'embedding', $query_vec, $limit) "
                    "YIELD node "
                    + vec_status_filter +
                    f"RETURN node.{id_field} "
                    "LIMIT $limit"
                )

            def _query_nodes(sig: str):
                return graph.query(
                    _sig_cypher(sig),
                    params={"query_vec": query_vec, "limit": limit},
                    timeout=timeout_ms,
                ).result_set

            def _signature_failure(msg: str) -> bool:
                return (
                    "invalid arguments" in msg
                    or "not registered" in msg
                    or "type mismatch" in msg
                    or "unknown procedure" in msg
                )

            # #1359: `queryNodes` argument order varies by engine version.
            # The projection records which API succeeded at index-creation
            # time (FalkorProjection._vector_index_api, threaded in by the
            # caller): 'cypher' engines go straight to signature B, skipping
            # the guaranteed-failing sig-A attempt (one failed round trip
            # saved per query); 'procedure' engines and unrecorded (None)
            # keep sig A first. A signature failure on the preferred form
            # retries the other; anything else falls through to brute-force.
            preferred = "B" if vector_index_api == "cypher" else "A"
            fallback = "A" if preferred == "B" else "B"
            try:
                rows = _query_nodes(preferred)
                sig = preferred
            except Exception as e:
                if not _signature_failure(str(e).lower()):
                    raise  # non-signature failure → brute-force below
                rows = _query_nodes(fallback)
                sig = fallback
            elapsed = (time.monotonic() - start) * 1000
            if elapsed > timeout_ms:
                # #561: latency warning only — keep the rows.
                logger.warning("Vector query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
            _breaker_record("vector", True)
            if sig == "B":
                # Engine-native scores: cosine similarity in (-1, 1] on
                # similarityFunction:'cosine' indexes — clamp to [0, 1]
                # and pass through in index rank order.
                out = []
                for row in rows:
                    try:
                        score = float(row[1])
                    except (IndexError, TypeError, ValueError):
                        score = 0.0
                    out.append((row[0], max(0.0, min(1.0, score))))
                _record(ran=True, degraded=False, reason="ok", count=len(out))
                return out
            # Index results are ranked by similarity; assign rank-based scores.
            # RRF fusion uses rank not absolute scores; single-strategy mode
            # gets reasonable descending ordering.
            total = len(rows)
            _record(ran=True, degraded=False, reason="ok", count=total)
            return [(row[0], 1.0 - (i / max(total, 1))) for i, row in enumerate(rows)]
        except Exception as e:
            msg = str(e).lower()
            if "index" in msg or "not found" in msg or "does not exist" in msg:
                logger.info("Vector index not available — falling back to brute-force")
            else:
                # #1359: log the fallback at debug (not warning) when the
                # index-accelerated path merely isn't supported — brute-force
                # is the documented degradation, not an error.
                logger.debug("Vector index query failed, falling back to brute-force: %s", e)
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
        # #689: retracted Points must not leak into vector results.
        if label == "Point":
            bf_status_clause = ("" if excluded_statuses == ()
                                else f" AND {_exclude_status_clause('n', excluded_statuses or TERMINAL_EXCLUDED_STATUSES)}")
        else:
            bf_status_clause = ""
        cypher = (
            f"MATCH (n:{label}) "
            "WHERE n.embedding IS NOT NULL" + bf_status_clause +
            " WITH vecf32($query_vec) AS _qv, n "
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
        if rows:
            _record(ran=True, degraded=False, reason="ok", count=len(rows))
            return [(row[0], float(row[1])) for row in rows]
        # R3 (#1542) D4: the explicit zero-row guard — an all-no-embedding
        # graph returns [] WITHOUT raising (the except catch below never
        # fires for the real empty case). Cheap count(p.embedding) guard
        # distinguishes no_embeddings (zero embedded points) from
        # empty_results (embedded points present, no matches).
        try:
            cnt_rows = graph.query(
                f"MATCH (n:{label}) WHERE n.embedding IS NOT NULL "
                "RETURN count(*)", timeout=timeout_ms).result_set
            embedded = cnt_rows[0][0] if cnt_rows else 0
        except Exception:  # noqa: BLE001, RUF100
            embedded = 0
        _record(ran=True, degraded=(embedded == 0),
                reason=("no_embeddings" if embedded == 0 else "empty_results"),
                count=0)
        return []
    except Exception as e:
        msg = str(e).lower()
        if "index" in msg or "not found" in msg or "does not exist" in msg:
            logger.info("Vector index not available — skipping vector strategy")
            _breaker_record("vector", True)
            _record(ran=True, degraded=True, reason="index_missing", count=0)
        elif "embedding" in msg and "null" in msg:
            logger.info("No Points with embeddings — skipping vector strategy")
            _breaker_record("vector", True)
            _record(ran=True, degraded=True, reason="no_embeddings", count=0)
        else:
            logger.warning("Vector query failed: %s", e)
            _breaker_record("vector", False)
            _record(ran=True, degraded=True, reason="query_failed", count=0)
        return []


def run_structural_query(
    graph, kind: str | None,
    entity_type: str = "point", limit: int = 20, timeout_ms: int = 500,
    excluded_statuses: tuple | None = None,
    leg_trace: list[dict] | None = None,
) -> list[tuple[str, float]]:
    """Run structural/kind query via range indexes.

    entity_type: 'point' (filters on pointKind), 'event' (eventKind),
                 'subject' (subjectKind), 'operator' (op_type, Point nodes with is_operator=true),
                 'source' (sourceKind), 'document' (documentKind), 'object' (objectKind).
    Returns matching entities with a score of 1.0 (exact match) or 0.5 (partial match).

    R3 (#1542) D4: ``leg_trace`` — when provided, appends a per-leg entry
    at every exit branch (ok / empty_results / index_missing / query_failed /
    breaker_open). Default None = no trace.

    #249: the driver-level timeout is passed through so a wedged DB cannot
    hang the structural strategy (the third leg of the degradation chain);
    the per-strategy breaker short-circuits after consecutive failures.
    """
    def _record(*, ran: bool, degraded: bool, reason: str | None,
                count: int) -> None:
        if leg_trace is not None:
            leg_trace.append(_trace_entry("structural", ran=ran,
                                          degraded=degraded,
                                          reason=reason, count=count))

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
        _record(ran=False, degraded=True, reason="breaker_open", count=0)
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
            _record(ran=True, degraded=False, reason="empty_results", count=0)
            return []

        # #1391: terminal-status exclusion (after the no-filters gate so a
        # kind-less broad scan keeps main's early-return behavior — the
        # status clause must not become the sole condition that fires the
        # scan). include_terminal opts out for audit/history queries.
        if excluded_statuses != () and label_str == "Point":
            conditions.append(_exclude_status_clause(
                "n", excluded_statuses or TERMINAL_EXCLUDED_STATUSES))
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
        _record(ran=True, degraded=False,
                reason=("ok" if results else "empty_results"), count=len(results))
        return results
    except Exception as e:
        msg = str(e).lower()
        if "index" in msg or "not found" in msg or "does not exist" in msg:
            logger.info("Structural index not available — skipping structural strategy")
            _breaker_record("structural", True)
            _record(ran=True, degraded=True, reason="index_missing", count=0)
        else:
            logger.warning("Structural query failed: %s", e)
            _breaker_record("structural", False)
            _record(ran=True, degraded=True, reason="query_failed", count=0)
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
    elevated_timeout_ms: int | None = None,
    vector_index_api: str | None = None,
    excluded_statuses: tuple | None = None,
    leg_trace: list[dict] | None = None,
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

    elevated_timeout_ms: benchmark override (issue #316). Default None = the
        pre-registered 500ms collective cap (as_completed(timeout=0.5)). When
        set (e.g. 5000ms), it is threaded into all THREE spots — the per-runner
        driver timeout, the structural runner timeout, and the as_completed
        deadline — so a benchmark can measure true completion without the cap
        truncating the tail. Default-off: production callers never pass it and
        behavior is byte-identical.

    vector_index_api: 'procedure' | 'cypher' | None — recorded on the
        projection at index-creation time (FalkorProjection._vector_index_api,
        #1359); forwarded to run_vector_query so Cypher-native engines skip
        the failing signature-A attempt.

    Returns:
        {strategy_name: [(id, score), ...]} — only strategies that succeeded

    R3 (#1542) D4: ``leg_trace`` — when provided, per-strategy entries are
    merged into it in FIXED order (fts, vector, structural). Runner threads
    record into their own PRIVATE list (a cancelled-but-running worker can
    never append to the shared trace after the merge); the merge reads only
    ``future.done()`` futures via ``result(timeout=0)`` so it is
    non-blocking — a still-running worker is never joined past the deadline;
    on ``TimeoutError`` it is discarded and self-recorded as
    ``reason="timeout", ran=True, degraded=True`` (never absent). Default
    None = no trace (byte-identical behavior).
    """
    import concurrent.futures

    results: dict[str, list[tuple[str, float]]] = {}
    futures: dict[concurrent.futures.Future, str] = {}
    trace_active = leg_trace is not None
    #: per-strategy private lists (workers record here, never into the
    #: caller's shared trace — the data-race guard, review P1).
    private: dict[str, list[dict]] = {}
    if trace_active:
        for _name in ("fts", "vector", "structural"):
            private[_name] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        # Submit all enabled strategies in parallel
        runner_timeout = int(elevated_timeout_ms or 500)
        # R3 (#1542) D4: the per-strategy private list is passed ONLY when
        # tracing is active — a default (leg_trace=None) caller sees
        # byte-identical submit kwargs (the bench fakes in
        # tests/bench/test_degradation_chain.py pin this).
        def _runner_kwargs(name: str) -> dict:
            return ({"leg_trace": private.get(name)} if trace_active else {})

        # #1391: the caller's exclusion applies uniformly (best-match AND
        # full-scan). Full-scan completeness callers (the #1353 supersede-
        # structure surface) opt in via excluded_statuses=() (include_terminal).
        if strategies.get("fts") and query:
            futures[executor.submit(
                run_fts_query, graph, query, entity_type=entity_type, limit=limit,
                timeout_ms=runner_timeout, excluded_statuses=excluded_statuses,
                **_runner_kwargs("fts"),
            )] = "fts"

        if strategies.get("vector") and query_vec:
            futures[executor.submit(
                run_vector_query, graph, query_vec, limit=limit, is_embedded=is_embedded,
                entity_type=entity_type, timeout_ms=runner_timeout,
                vector_index_api=vector_index_api, excluded_statuses=excluded_statuses,
                **_runner_kwargs("vector"),
            )] = "vector"

        if strategies.get("structural"):
            futures[executor.submit(
                run_structural_query, graph, kind, entity_type=entity_type, limit=limit,
                timeout_ms=runner_timeout, excluded_statuses=excluded_statuses,
                **_runner_kwargs("structural"),
            )] = "structural"

        # Collect results with 500ms total timeout across all strategies.
        # as_completed(timeout=0.5) raises TimeoutError if any future
        # hasn't completed within 500ms from the start of iteration.
        # #316: elevated_timeout_ms threads the benchmark cap into the
        # deadline too (default-off — production callers keep 0.5s).
        deadline = (elevated_timeout_ms or 500) / 1000.0
        timed_out: set[str] = set()
        try:
            for future in concurrent.futures.as_completed(futures, timeout=deadline):
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
            # R3 (#1542) D4: a future not completed by the deadline is
            # discarded AND self-recorded as ``timeout`` (never absent). The
            # merge reads only done futures via result(timeout=0) — a
            # still-running worker is never joined past the deadline; its
            # late private-list entries are discarded (timed_out skip below).
            for f in list(futures):
                strategy_name = futures[f]
                if strategy_name in results:
                    continue
                if f.done():
                    try:
                        strategy_results = f.result(timeout=0)
                        if strategy_results:
                            results[strategy_name] = strategy_results
                    except Exception as e:
                        logger.warning("Strategy execution failed: %s — degraded", e)
                else:
                    timed_out.add(strategy_name)
                    f.cancel()
        except Exception as e:
            logger.warning("Strategy execution failed: %s — degraded", e)

        # R3 (#1542) D4: merge the private lists in FIXED order (fts, vector,
        # structural) — as_completed yields in COMPLETION order, so the merge
        # must not follow it. Timed-out strategies get their self-recorded
        # timeout entry here (fixed order too); a cancelled-but-running
        # worker's late append to its PRIVATE list cannot land in the trace.
        if trace_active:
            for strategy_name in ("fts", "vector", "structural"):
                if strategy_name in timed_out:
                    leg_trace.append(_trace_entry(
                        strategy_name, ran=True, degraded=True,
                        reason="timeout", count=0))
                    continue
                entries = private.get(strategy_name)
                if entries:
                    leg_trace.extend(entries)

    return results


# ── EP annotation ────────────────────────────────────────────────────────────

# Variance threshold above which a claim's posterior is considered contested.
# Must match TortoiseEP.get_contested_claims (tortoise/ep.py).
CONTESTED_VARIANCE_THRESHOLD = 0.04

# Driver-level timeout for decoration queries (ms). The decoration runs OUTSIDE
# the per-strategy circuit breakers (those guard degradation_chain); a server-side
# kill per query bounds a hung FalkorDB's impact on search latency (#1353 review).
_DECORATION_TIMEOUT_MS = 200


def _created_sort_key(value):
    """Sortable key for createdAt values — safe across mixed formats.

    The codebase writes ISO-8601 strings (projection) AND numeric epoch
    (RedisGraph `timestamp()` in seeded corpora) — a graph can hold both.
    Comparing them directly raises TypeError in py3. ISO strings are parsed
    to epoch so mixed-format graphs compare by REAL instant (not format);
    unparseable values bucket last, deterministically.
    """
    if isinstance(value, (int, float)):
        return (0, float(value))
    s = str(value or "")
    if s and s[0].isdigit() and len(s) >= 10 and ("-" in s or "T" in s):
        try:
            from datetime import datetime
            return (0, datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    return (1, s)


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
    variance/contested are computed from the PERSISTED posterior (posterior_alpha/beta, falling back to ep_alpha/beta priors)
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
            "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0) AS alpha, coalesce(n.posterior_beta, n.ep_beta, 1.0) AS beta, "
            "  (n.posterior_alpha IS NOT NULL OR n.ep_alpha IS NOT NULL) AS has_ep "
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
                has_ep=bool(has_ep),
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
            "  AND other.is_operator = false "
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
            other_idx = row[8] if len(row) > 8 else None  # noqa: F841

            # Determine direction: idx=0 = source, idx>0 = target.
            # Our point is source → relationship is outgoing.
            # Our point is target + other is source → relationship is incoming.
            if n_idx is not None and n_idx == 0:  # noqa: SIM108
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


def get_relationships_bounded(
    graph,
    point_ids: list[str],
    per_point_cap: int = 10,
    global_budget: int = 140,
    raw_cap: int = 3000,
    per_op_cap: int = 10,
    expand_top_k: int = 14,
) -> dict[str, list[dict]]:
    """Bounded, state-centric relationship decoration (#1353, D1-D14).

    Replaces the unbounded 2-hop fan-out at the search-decoration call site:
    the old query re-expanded every operator's full neighborhood PER RESULT
    POINT (n_results × operator-degree → ~122K dicts dense @ limit=100).
    This decouples the hops and dedupes operators across result points:

      Q1a   point → its operator edges (bounded by point degree)
      Q1b   CORRECTS edges in/out (supersede structure, direct point→point)
      Q2b   mitigations per operator (ids also exclude mitigation points from Q2)
      Q2    endpoints per operator (deduped), NAND-first, raw-capped
      Q2f   per-operator family counts (family_size)

    Class-aware cap: NAND edges, contested peers, superseded/retracted peers,
    mitigated_by and CORRECTS are ALWAYS kept — exempt from the per-point cap
    AND the global budget. The per-point cap (default 10) and global budget
    (default 140) govern the deduped IMPL support-mass only; exhaustion
    degrades tail results to structure counts (no peer lists).

    Peer EP state is derived IN-QUERY from coalesced posterior/ep alpha+beta
    (annotate_ep_batch parity — never NULL α/β, never a second annotation
    pass over thousands of peers). confidence = posterior mean α/(α+β);
    variance = _beta_variance; contested = has_ep AND variance > threshold.

    Entries keep the legacy keys (predicate, mechanism, operator_id,
    related_id, related_kind, direction) and add role, peer, family_size,
    op_created_at. related_content is intentionally ABSENT in the list view
    (D5 — full content via expand_relationships, D14).

    Contested coverage: contested peers are computed EXACTLY in Q2-crit — the
    in-Cypher variance on the coalesced persisted α/β (same formula as
    `_beta_variance`/TortoiseEP), so contested is cap-exempt with no sampling.
    NAND, terminal-status, mitigated and CORRECTS classes are always complete.

    get_relationships() is intentionally UNTOUCHED (D12) — topic_summarization
    needs full NAND completeness for disputed-pair detection.
    """
    if not point_ids:
        return {}

    rels: dict[str, list[dict]] = {pid: [] for pid in point_ids}

    try:
        # Q1a: operator edges per point (bounded by point degree).
        # Retracted operators excluded (operator terminal state — their edges
        # are not live epistemic structure).
        rows = graph.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "MATCH (n)-[r:IMPL|NAND|hasPart]-(op:Point {is_operator:true}) "
            f"WHERE {_exclude_status_clause('op')} "
            "RETURN n.id, type(r) AS et, r.idx AS n_idx, op.id AS op_id, "
            "  op.op_type AS mechanism, coalesce(op.label, '') AS predicate, "
            "  op.createdAt AS op_created",
            params={"ids": point_ids},
            timeout=_DECORATION_TIMEOUT_MS,
        ).result_set
        point_ops: dict[str, list[tuple]] = {pid: [] for pid in point_ids}
        op_ids: set[str] = set()
        for row in rows:
            pid = row[0]
            point_ops[pid].append((row[1], row[2], row[3], row[4] or "IMPL", row[5], row[6]))
            op_ids.add(row[3])

        # Q1b: CORRECTS edges (direct point→point supersede links).
        # Chained OPTIONAL MATCH can cartesian (m outgoing × k incoming rows)
        # — dedupe by id in Python below.
        corrects_out: dict[str, list[tuple]] = {pid: [] for pid in point_ids}
        corrects_in: dict[str, list[tuple]] = {pid: [] for pid in point_ids}
        if point_ids:
            rows = graph.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "OPTIONAL MATCH (n)-[r:CORRECTS]->(old:Point) "
                "OPTIONAL MATCH (new:Point)-[r2:CORRECTS]->(n) "
                "RETURN n.id, old.id, old.status, old.createdAt, "
                "  new.id, new.status, new.createdAt",
                params={"ids": point_ids},
                timeout=_DECORATION_TIMEOUT_MS,
            ).result_set
            for row in rows:
                pid = row[0]
                old_id, old_status, old_created = row[1], row[2], row[3]
                new_id, new_status, new_created = row[4], row[5], row[6]
                if old_id and old_id not in {o[0] for o in corrects_out[pid]}:
                    corrects_out[pid].append((old_id, old_status, old_created))
                if new_id and new_id not in {n[0] for n in corrects_in[pid]}:
                    corrects_in[pid].append((new_id, new_status, new_created))

        # Q2b: mitigations per operator — ids are excluded from endpoint
        # expansion in Cypher (direction-agnostic: legacy inbound mitigated_by
        # graphs are covered by the NOT pattern).
        op_mitigations: dict[str, list[dict]] = {}
        if op_ids:
            rows = graph.query(
                "MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids "
                "MATCH (op)-[:mitigated_by]->(m:Point) "
                "RETURN op.id, m.id, m.status, m.createdAt, m.content",
                params={"op_ids": list(op_ids)},
                timeout=_DECORATION_TIMEOUT_MS,
            ).result_set
            for row in rows:
                op_id = row[0]
                op_mitigations.setdefault(op_id, []).append({
                    "id": row[1], "status": row[2] or "",
                    "created_at": row[3], "snippet": (row[4] or "")[:120],
                })

        # Q2-crit: critical-class endpoints — NAND edges, terminal-status peers,
        # AND contested peers (in-Cypher variance on the coalesced α/β — exact,
        # no per-op sampling). COMPLETE, bounded only by raw_cap (safety valve):
        # these classes must never be dropped by the per-point/global/per-op
        # caps (D3/D4). Low volume in practice.
        op_crit: dict[str, dict[str, tuple]] = {}
        if op_ids:
            rows = graph.query(
                "MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids "
                "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point) "
                "WHERE (other.is_operator = false OR other.is_operator IS NULL) "
                "  AND NOT (op)-[:mitigated_by]->(other) "
                "  AND (type(r2) = 'NAND' "
                "       OR other.status IN ['superseded','retracted','deprecated'] "
                "       OR ((other.posterior_alpha IS NOT NULL OR other.ep_alpha IS NOT NULL) "
                "           AND (coalesce(other.posterior_alpha, other.ep_alpha, 1.0) * coalesce(other.posterior_beta, other.ep_beta, 1.0)) "
                "               / ((coalesce(other.posterior_alpha, other.ep_alpha, 1.0) + coalesce(other.posterior_beta, other.ep_beta, 1.0)) ^ 2 "
                "                  * (coalesce(other.posterior_alpha, other.ep_alpha, 1.0) + coalesce(other.posterior_beta, other.ep_beta, 1.0) + 1)) > $contested_threshold)) "
                "RETURN op.id, type(r2) AS et, r2.idx AS other_idx, other.id, "
                "  other.pointKind, other.status, "
                "  coalesce(other.posterior_alpha, other.ep_alpha, 1.0), "
                "  coalesce(other.posterior_beta, other.ep_beta, 1.0), "
                "  (other.posterior_alpha IS NOT NULL OR other.ep_alpha IS NOT NULL), "
                "  other.createdAt "
                "LIMIT $raw_cap",
                params={"op_ids": list(op_ids), "raw_cap": raw_cap,
                        "contested_threshold": CONTESTED_VARIANCE_THRESHOLD},
                timeout=_DECORATION_TIMEOUT_MS,
            ).result_set
            for row in rows:
                op_crit.setdefault(row[0], {})[row[3]] = (
                    row[1], row[2], row[4] or "", row[5] or "",
                    float(row[6]), float(row[7]), bool(row[8]), row[9],
                )

        # The operators of the TOP-K result points are the ones that get
        # support endpoint expansion. The global budget (140 ≈ 14 × 10)
        # makes tail points' peer lists redundant — they degrade to structure
        # counts (D3), so their operators are not expanded (this is what keeps
        # limit=100 affordable).
        expand_ops: set[str] = set()
        for pid in point_ids[:expand_top_k]:
            for (_et, _idx, op_id, _mech, _pred, _created) in point_ops.get(pid, []):
                expand_ops.add(op_id)

        # Q2-support: per-op-capped support endpoints (CALL subquery = per-op
        # LIMIT, no global sort) for the expanded operators. Contested peers
        # are already covered EXACTLY by Q2-crit — this fetch is pure filler.
        op_support: dict[str, dict[str, tuple]] = {}
        if expand_ops:
            rows = graph.query(
                "MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids "
                "CALL { WITH op MATCH (op)-[r2:IMPL|hasPart]-(other:Point) "
                "WHERE (other.is_operator = false OR other.is_operator IS NULL) "
                "  AND NOT (op)-[:mitigated_by]->(other) "
                "RETURN r2, other LIMIT $per_op } "
                "RETURN op.id, type(r2) AS et, r2.idx AS other_idx, other.id, "
                "  other.pointKind, other.status, "
                "  coalesce(other.posterior_alpha, other.ep_alpha, 1.0), "
                "  coalesce(other.posterior_beta, other.ep_beta, 1.0), "
                "  (other.posterior_alpha IS NOT NULL OR other.ep_alpha IS NOT NULL), "
                "  other.createdAt",
                params={"op_ids": list(expand_ops), "per_op": per_op_cap},
                timeout=_DECORATION_TIMEOUT_MS,
            ).result_set
            for row in rows:
                op_support.setdefault(row[0], {})[row[3]] = (
                    row[1], row[2], row[4] or "", row[5] or "",
                    float(row[6]), float(row[7]), bool(row[8]), row[9],
                )

        # Unified endpoint store: critical classes first, then support filler
        # (same tuple shape — dict-merge dedupes).
        op_endpoints: dict[str, dict[str, tuple]] = {}
        for op_id in op_ids:
            merged: dict[str, tuple] = {}
            for src in (op_crit, op_support):
                merged.update(src.get(op_id, {}))
            op_endpoints[op_id] = merged

        # Q2f: family sizes per (op, edge-type) for ALL result operators.
        # Plain aggregate WITHOUT the mitigation-exclusion pattern (the NOT
        # pattern cost ~50ms here) — family is approximate disclosure and
        # mitigations are rare (their count inflates family by exactly the
        # separately-listed mitigations, documented). Covers tail points'
        # private operators so the D3 structure-count degradation is real.
        op_family: dict[tuple[str, str], int] = {}
        if op_ids:
            rows = graph.query(
                "MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids "
                "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point) "
                "WHERE (other.is_operator = false OR other.is_operator IS NULL) "
                "RETURN op.id, type(r2) AS et, count(other)",
                params={"op_ids": list(op_ids)},
                timeout=_DECORATION_TIMEOUT_MS,
            ).result_set
            for row in rows:
                op_family[(row[0], row[1])] = int(row[2])

        # ── Assembly: class-aware, epistemically-prioritized ────────────────
        # Priority (D4): NAND > contested > superseded/retracted >
        # mitigated_by/CORRECTS > recency > deduped IMPL support-mass.
        global_support_used = 0
        for pid in point_ids:
            criticals: list[dict] = []
            support: list[dict] = []

            for (et, n_idx, op_id, mechanism, predicate, op_created) in point_ops.get(pid, []):  # noqa: B007
                for other_id, ep in op_endpoints.get(op_id, {}).items():
                    if other_id == pid:
                        continue  # self-peer exclusion
                    et2, other_idx, kind, status, alpha, beta, has_ep, created = ep  # noqa: RUF059
                    variance = _beta_variance(alpha, beta)
                    contested = has_ep and variance > CONTESTED_VARIANCE_THRESHOLD
                    role = "source" if (n_idx is not None and n_idx == 0) else "target"
                    direction = "outgoing" if role == "source" else "incoming"
                    entry = {
                        "predicate": predicate if predicate else "",
                        "mechanism": mechanism,
                        "operator_id": op_id,
                        "related_id": other_id,
                        "related_kind": kind,
                        "direction": direction,
                        "role": role,
                        "peer": {
                            "id": other_id,
                            "kind": kind,
                            "status": status,
                            # Unmeasured (no persisted EP) peers carry None —
                            # never a fabricated Beta(1,1) posterior (0.5/0.0833
                            # would be internally contradictory: variance above
                            # the contested threshold but contested=false).
                            "confidence": round(alpha / (alpha + beta), 4) if has_ep else None,
                            "variance": round(variance, 6) if has_ep else None,
                            "contested": contested,
                            "created_at": created,
                        },
                        "family_size": op_family.get((op_id, et2), 0),
                        "op_created_at": op_created,
                    }
                    if et2 == "NAND" or contested or status in ("superseded", "retracted", "deprecated"):
                        criticals.append(entry)
                    else:
                        support.append(entry)

                for m in op_mitigations.get(op_id, []):
                    criticals.append({
                        "predicate": "",
                        "mechanism": "mitigated_by",
                        "operator_id": op_id,
                        "related_id": m["id"],
                        "related_kind": "mitigation",
                        "direction": "incoming",
                        "role": "target",
                        "peer": {
                            "id": m["id"], "kind": "mitigation",
                            "status": m["status"], "confidence": None,
                            "variance": None, "contested": False,
                            "created_at": m["created_at"],
                        },
                        "family_size": 0,
                        "op_created_at": op_created,
                    })

            # CORRECTS entries (direct edges — no operator_id/family_size).
            for (old_id, old_status, old_created) in corrects_out.get(pid, []):
                criticals.append({
                    "predicate": "", "mechanism": "CORRECTS", "operator_id": "",
                    "related_id": old_id, "related_kind": "point",
                    "direction": "outgoing", "role": "source",
                    "peer": {"id": old_id, "kind": "point", "status": old_status or "",
                              "confidence": None, "variance": None,
                              "contested": False, "created_at": old_created},
                    "family_size": 0, "op_created_at": None,
                })
            for (new_id, new_status, new_created) in corrects_in.get(pid, []):
                criticals.append({
                    "predicate": "", "mechanism": "CORRECTS", "operator_id": "",
                    "related_id": new_id, "related_kind": "point",
                    "direction": "incoming", "role": "target",
                    "peer": {"id": new_id, "kind": "point", "status": new_status or "",
                              "confidence": None, "variance": None,
                              "contested": False, "created_at": new_created},
                    "family_size": 0, "op_created_at": None,
                })

            # Support-mass: recency-ordered (mixed-format safe), deduped, capped
            # by BOTH budgets. Criticals are exempt from the per-point cap —
            # they do NOT consume per-point room (D3: caps govern support only).
            support.sort(key=lambda e: _created_sort_key(e["peer"].get("created_at")), reverse=True)
            seen: set[tuple] = set()
            deduped: list[dict] = []
            for e in support:
                key = (e["operator_id"], e["mechanism"], e["related_id"])
                if key not in seen:
                    seen.add(key)
                    deduped.append(e)
            support = deduped

            entries = list(criticals)
            per_point_room = per_point_cap  # criticals are exempt — D3
            global_room = max(0, global_budget - global_support_used)
            keep = min(per_point_room, global_room, len(support))
            entries.extend(support[:keep])
            global_support_used += keep

            # Tail degradation (beyond expand_top_k, or all support capped away):
            # no peer entries shown → disclose family counts per mechanism so the
            # agent still knows the structural weight (D3). Points WITH peer
            # entries disclose their full family via per-entry family_size.
            if not any("peer" in e for e in entries) and point_ops.get(pid):
                # family per mechanism across this point's operators
                mech_family = Counter()
                for (et, _idx, op_id, mechanism, _pred, _created) in point_ops.get(pid, []):
                    mech_family[mechanism] += op_family.get((op_id, et), 0)
                crit_mech = Counter(e["mechanism"] for e in criticals)
                for mech, fam in mech_family.items():
                    leftover = fam - crit_mech.get(mech, 0)
                    if leftover > 0:
                        entries.append({"predicate": "", "mechanism": mech, "count": leftover})

            rels[pid] = entries
    except Exception:
        logger.warning("Bounded relationship query failed", exc_info=True)

    return rels


def fetch_point_epistemic_state(graph, point_ids: list[str]) -> dict[str, dict]:
    """Fetch promoted epistemic state for a batch of Points (#1353 D8/D10).

    Returns {pid: {status, superseded_by, supersedes, subject}} where:
      status         — n.status (live/superseded/deprecated/retracted/draft)
      superseded_by  — {id, content_snippet, created_at} of the newest superseding
                       claim (incoming CORRECTS) or None
      supersedes     — [{id, content_snippet, created_at}] of replaced claims
                       (outgoing CORRECTS)
      subject        — {id, name, kind} from the point's OWN aboutSubject edge, or
                       (fallback) its source-event's aboutSubject edge — ≤1 hop only.
                       #1417: the event hop is resolved via the point's eventId
                       PROPERTY (the provenance surface), never via an aboutEvent
                       edge (that edge is content-aboutness, ONTOLOGY §3.4).
                       NEVER derived through operator chains (fail-closed, D10):
                       absent = honestly unknown, never wrong-via-chain.

    Chained OPTIONAL MATCHes can cartesian — deduped in Python. Rows are small
    (per-point CORRECTS/subject counts are low-volume).
    """
    if not point_ids:
        return {}

    out: dict[str, dict] = {}
    try:
        rows = graph.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "OPTIONAL MATCH (n)-[:aboutSubject]->(s:Subject) "
            # #1417: provenance hop via the point's eventId property (the
            # provenance surface) — NOT an aboutEvent edge, which is reserved
            # for content-aboutness (ONTOLOGY §3.4). Legacy aboutEvent-only
            # points (no eventId property) no longer resolve — the fallback
            # now requires the eventId property (review P2).
            "OPTIONAL MATCH (ev:Event) WHERE ev.eventId = n.eventId "
            "OPTIONAL MATCH (ev)-[:aboutSubject]->(es:Subject) "
            "OPTIONAL MATCH (n)-[r:CORRECTS]->(old:Point) "
            "OPTIONAL MATCH (new:Point)-[r2:CORRECTS]->(n) "
            "RETURN n.id, n.status, "
            "  old.id, old.content, old.createdAt, "
            "  new.id, new.status, new.content, new.createdAt, "
            "  s.id, s.name, s.subjectKind, "
            "  es.id, es.name, es.subjectKind",
            params={"ids": point_ids},
            timeout=_DECORATION_TIMEOUT_MS,
        ).result_set

        for row in rows:
            pid = row[0]
            st = out.setdefault(pid, {
                "status": row[1] or "",
                "superseded_by": None,
                "supersedes": [],
                "subject": None,
            })
            old_id, old_content, old_created = row[2], row[3], row[4]
            new_id, new_status, new_content, new_created = row[5], row[6], row[7], row[8]
            if old_id and old_id not in {s["id"] for s in st["supersedes"]}:
                st["supersedes"].append({
                    "id": old_id,
                    "content_snippet": (old_content or "")[:120],
                    "created_at": old_created,
                })
            if new_id:
                cand = {
                    "id": new_id,
                    "content_snippet": (new_content or "")[:120],
                    "created_at": new_created,
                }
                # A retracted correcting point is NOT superseding authority.
                if new_status == "retracted":
                    pass
                elif st["superseded_by"] is None or _created_sort_key(new_created) > _created_sort_key(
                        st["superseded_by"].get("created_at")):
                    st["superseded_by"] = cand
            # Subject: own aboutSubject wins; event's is the ≤1-hop fallback.
            s_id, s_name, s_kind = row[9], row[10], row[11]
            es_id, es_name, es_kind = row[12], row[13], row[14]
            if st["subject"] is None and s_id:
                st["subject"] = {"id": s_id, "name": s_name or "", "kind": s_kind or ""}
            elif st["subject"] is None and es_id:
                st["subject"] = {"id": es_id, "name": es_name or "", "kind": es_kind or ""}
    except Exception:
        logger.warning("Epistemic-state fetch failed", exc_info=True)

    return out


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

    R2 (#1541) D4: the indexed text is ``index_text(content, search_keys)``
    — content ∪ search_keys — the embedded-stack counterpart of the real
    backend's multi-field Point FTS index (surface-11 normalization is a
    shared code path via tortoise.sparse, not a magic parity layer). Points
    dicts from ``self.query`` carry ``search_keys`` when present (additive;
    absent → byte-identical to the pre-R2 path). The returned payload keeps
    the REAL content (the alias text is index-only, never surfaced).
    """
    try:
        from tortoise.embeddings import search_points
        from .sparse import index_text
        meta = {p["id"]: p for p in points if p.get("id")}
        # R2: alias text is index-only — restore the real content on the
        # results (search_points echoes the dicts it was handed).
        real_content = {p["id"]: p.get("content", "") for p in points if p.get("id")}
        indexed = [
            {**p, "content": index_text(p.get("content"), p.get("search_keys"))}
            for p in points
        ]
        results = search_points(query, indexed, threshold=0.0, limit=limit)
        for r in results:
            r["content"] = real_content.get(r["id"], r["content"])
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
