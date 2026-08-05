<!-- issue-scoping: v5.1 double diamond + verify -->
## Confirmed Problem

**Root cause:** All Tortoise ranked-result surfaces (`tortoise_search`, `tortoise_suggest_entry_points`) use one-dimensional ranking — pure cosine similarity or string-match heuristics. Graph topology (EP confidences, operator edges, entity relationships) — which is Tortoise's unique structural advantage — is invisible to ranking. The session-entry-point use case is the most acute pain point: agents calling `suggest_entry_points("pricing")` get results ordered by keyword overlap, not by which sessions produced well-reasoned, high-confidence knowledge.

This is not just a session problem — it affects every ranked result. But sessions are the right MVP scope because:
1. The INSTANTIATES edge (Session→Object) is a graph-native signal no competitor has
2. Session ranking is the most common agent query pattern
3. The solution architecture (a composable `GraphRanker`) generalizes to Point search when ready

**Quality over convenience:** The easy path is adding inline boosts to existing functions. The right path is a clean `GraphRanker` abstraction that composes into all ranking surfaces and is independently testable.

## Verification Gates

### problem-verify: 1 cycle (internal synthesis — sub-agent API unavailable)
- **problem-diverge:** Generated 3 alternative framings. Converged on the broader framing (all ranked results lack graph signals, not just sessions) with session MVP scope.
- **problem-converge:** Evidence-based — the 2026-07-18 research doc independently validated this as Tortoise's "unique differentiator" with no competitor implementing anything similar.
- **Quality over convenience check:** PASS. The broader framing (all ranked results) was selected over the narrower framing (sessions only), even though it means designing for generality.

### solution-verify: 1 cycle (internal)
- **solution-diverge:** 3 approaches generated (inline boost, GraphRanker class, full RRF pipeline).
- **solution-converge:** GraphRanker class selected — quality over convenience. Rejected inline boosts as coupling ranking into already-large functions. Rejected full RRF pipeline as premature (BM25 indexing doesn't exist yet).
- **Genuineness:** All 3 approaches differ architecturally (modification vs new module vs pipeline refactor).

## Plan

### Problem Statement
Tortoise's ranking functions (`search_points`, `suggest_entry_points`) use only text similarity. They ignore graph topology — EP confidences, operator edges, entity relationships, and session→Object links. This means agents get results ordered by keyword match quality, not by epistemic quality. A well-reasoned claim with high EP confidence ranks below a keyword-dense but unverified claim. Sessions that produced rich knowledge graphs rank identically to sessions that produced nothing.

### Proposed Solution

**New module: `tortoise/ranking.py` — `GraphRanker` class**

A composable ranking booster that takes raw search results + a projection handle and returns reranked results with graph-informed scores.

```python
class GraphRanker:
    """Re-rank search results using graph topology signals.
    
    Composable into search_points(), suggest_entry_points(), and future 
    ranking surfaces. Each signal is independently configurable.
    """
    
    def __init__(self, projection, *, 
                 similarity_weight: float = 0.5,
                 graph_boost_weight: float = 0.35,
                 recency_weight: float = 0.15,
                 recency_half_life_days: float = 30.0):
        ...
    
    def rerank(self, results: list[dict], query: str) -> list[dict]:
        """Re-rank results. Each result gets:
        
        final_score = α·normalize(similarity) 
                    + β·graph_boost(result) 
                    + γ·recency_decay(result)
        
        Returns sorted by final_score descending, with each result
        annotated with {similarity, graph_boost, recency_boost, final_score}.
        """
    
    def graph_boost(self, result: dict) -> float:
        """Compute graph-informed boost for a result.
        
        For Points:
        - EP confidence: confidence["mean"] * (1 - confidence["variance"])
        - Operator connectivity: log(1 + inbound_edges + outbound_edges)
        - Grounding value (if computed): n.grounding
        
        For sessions (future, via INSTANTIATES edges):
        - Number of INSTANTIATES edges (Objects produced)
        - EP confidence of connected Points
        - Number of Points produced
        
        Returns 0.0-1.0 normalized boost.
        """
    
    def recency_decay(self, result: dict) -> float:
        """Exponential decay: e^(-λ * age_days) where λ = ln(2)/half_life."""
```

### Three Graph Signals (MVP for sessions)

| Signal | Source | Weight | Rationale |
|--------|--------|--------|-----------|
| **INSTANTIATES count** | `MATCH (s:Session)-[:INSTANTIATES]->(o:Object) RETURN count(o)` | 0.4 | Sessions that produced Objects are valuable — they created reusable knowledge |
| **EP confidence of connected Points** | `MATCH (s)-[:PRODUCES]->(p:Point) RETURN avg(p.confidence)` | 0.4 | High-confidence claims = well-reasoned session output |
| **Recency decay** | `e^(-λ·days_ago)` with λ=ln(2)/30 | 0.2 | Recent sessions are more relevant; 30-day half-life |

### Implementation Steps

**Step 1: `tortoise/ranking.py` — GraphRanker class (~120 lines)**
- `rerank()` method with weighted signal fusion
- `graph_boost()` — dispatches to signal-specific methods
- `recency_decay()` — exponential decay function
- Unit-testable with mocked projection

**Step 2: Wire into `tortoise/embeddings.py:search_points()` (~15 lines)**
- Accept optional `graph_ranker` parameter
- After similarity sort, pass through `graph_ranker.rerank()`
- Backward-compatible: None → no graph boost

**Step 3: Wire into `tortoise/sdk.py:suggest_entry_points()` (~15 lines)**
- After confidence sort, pass through `graph_ranker.rerank()`
- Same backward-compatible pattern

**Step 4: Graph signal queries in `tortoise/projection.py` (~40 lines)**
- `get_session_object_count(session_id)` — INSTANTIATES edge count
- `get_session_point_confidence(session_id)` — avg EP confidence of connected Points
- These become the data sources for `GraphRanker.graph_boost()`

**Step 5: Tests (~100 lines)**
- `tests/test_ranking.py` — unit tests for GraphRanker math
- `tests/test_tortoise_search.py` — integration tests with graph boost enabled
- `tests/test_suggest_entry_points.py` — integration tests with graph boost enabled

### Acceptance Criteria

1. **AC1:** `tortoise_search` results with graph boost rank high-EP-confidence Points above low-confidence Points with identical similarity scores
2. **AC2:** `tortoise_suggest_entry_points` results with graph boost rank sessions with INSTANTIATES edges above sessions without, all else equal
3. **AC3:** Recency decay correctly demotes old results: a 60-day-old result with same similarity+graph_score as a 1-day-old result ranks lower
4. **AC4:** Backward-compatible: when `graph_ranker=None`, behavior is identical to current
5. **AC5:** All three weights (α, β, γ) are configurable at construction time
6. **AC6:** GraphRanker is independently unit-testable without a live FalkorDB instance

### Testing Strategy
- **Unit:** `test_ranking.py` — test `rerank()` with mock results dicts, verify score math
- **Integration:** `test_tortoise_search.py` — seed Points with varying EP confidences, verify ranking order
- **Integration:** `test_suggest_entry_points.py` — seed sessions with/without INSTANTIATES edges, verify ranking order
- **Regression:** Existing search/suggest tests must pass unchanged

### Runtime Prerequisites
- EP confidence must be computed before graph boost is meaningful (uncalibrated Points get neutral boost of 0.0)
- INSTANTIATES edges must exist (this is a dependency on the session→Object linking from #7740 or equivalent)
- No new dependencies required (all graph traversal uses existing FalkorDB Cypher queries)

## Rejected Alternatives

### Approach A: Inline boost in existing functions
**Rejected because:** Couples ranking logic into already-large functions (`search_points` is 44 lines, `suggest_entry_points` is 38 lines). Makes testing harder — ranking math can't be tested independently of embedding models or DB setup. Violates single-responsibility. Would need to be refactored out when BM25 or cross-encoder ranking is added later.

**When this WOULD have been better:** If this were a quick experiment to validate that graph signals improve ranking at all. But the 2026-07-18 research doc already validated the concept. We're building infrastructure, not experimenting.

### Approach C: Full RRF pipeline (BM25 + vector + graph)
**Rejected because:** Premature. BM25 indexing doesn't exist yet in Tortoise (it's a separate recommendation R2 from the research doc). Building RRF without BM25 is just vector+graph fusion — which is what Approach B does but with cleaner architecture. Full pipeline is the right end-state but wrong starting point.

**When this WOULD have been better:** When BM25 indexing is implemented (likely next cycle after this issue). Then RRF becomes the natural extension: swap GraphRanker's `rerank()` to use RRF fusion instead of weighted sum.

## Wiring Check

| Touch Point | Type | Covered By | Status |
|-------------|------|------------|--------|
| `tortoise/embeddings.py:search_points()` | Function | Step 2 — optional `graph_ranker` param | ✅ |
| `tortoise/sdk.py:suggest_entry_points()` | Method | Step 3 — optional `graph_ranker` param | ✅ |
| `tortoise/sdk.py:search()` | Method | Inherits from search_points wiring | ✅ |
| `tortoise/projection.py` | Graph queries | Step 4 — get_session_* methods | ✅ |
| `tortoise/ep.py:TortoiseEP.compute_confidence()` | Data source | Already exists, used by graph_boost | ✅ |
| `tortoise/mcp_server.py:tortoise_search` | MCP tool | Pass-through — no changes needed | ✅ |
| `tortoise/mcp_server.py:tortoise_suggest_entry_points` | MCP tool | Pass-through — no changes needed | ✅ |
| INSTANTIATES edges | Graph schema | Dependency on #7740 or session linking | ⚠️ Requires coordination |
| EP confidence values on Points | Data | Already persisted via `compute_confidence()` | ✅ |
| `createdAt` field on Points | Data | Already exists, used by recency_decay | ✅ |
| sentence-transformers | External dep | Already imported, no change | ✅ |

**⚠️ Dependency note:** The INSTANTIATES edge (Session→Object) doesn't exist in the codebase yet. GraphRanker should be designed to gracefully handle missing edges (return 0.0 boost when no INSTANTIATES edges found). This decouples the ranker from the session wiring — it works immediately for Point EP confidence boost, and gets session boosts "for free" when #7740 lands.

## Review Cycle Log

**Cycle 1 (internal synthesis):**
- problem-diverge: Explored 3 framings. Codebase scout confirmed the diagnosis.
- problem-converge: Framing 1 (all ranked results) selected over narrower framing. Research doc independently validates.
- solution-diverge: 3 approaches generated.
- solution-converge: Approach B (GraphRanker class) selected over inline (A) and full RRF (C).

## Complexity

| Domain | Rating |
|--------|--------|
| Tier | **Standard** (upgraded from Micro — touches `tortoise/sdk.py`, `tortoise/embeddings.py`, shared infrastructure) |
| UX_RATING | low — no UI changes |
| ONTOLOGY_RATING | low — uses existing INSTANTIATES edges (planned), no new entity types |
| ARCH_RATING | medium — new module (`ranking.py`), new abstraction (GraphRanker), touches 4 existing files |

## Discovery: Adjacent Issues to File

During codebase scouting, two adjacent gaps were identified that should be separate issues:

1. **BM25 indexing for hybrid retrieval** — Currently only vector similarity exists. BM25 would catch exact entity name/error code matches that embeddings miss. This is R2 from the research doc. Should be filed as a separate issue (enables Approach C later).

2. **Cross-encoder reranking** — Optional LLM-powered reranking of top-K results for complex queries. R8 from the research doc. Separate issue, depends on LLM config availability.

📋 These should be filed as:
- #TBD: Add BM25 sparse retrieval alongside vector search
- #TBD: Cross-encoder reranking for top-K search results
