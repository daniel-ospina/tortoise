# Implementation Plan: Tortoise Hybrid Search & Retrieval (#7697)

**Date:** 2026-08-03
**Inputs:** Align Decision (PROCEED), Research Brief, Scope (17 items, 9 E2E), Test Design (#7735)

---

## 1. Agent Journeys (replaces User Journeys — no UI)

### Journey A: Reasoning Agent (Full-Scan Mode)
```
Agent: "Review the licensing-decision subgraph for weak spots"
→ tortoise_query(context="licensing-decision")  // no text query → full-scan
→ All 141 Points returned with EP breakdowns (confidence_mean + evidence + contention)
→ Agent scans for: duplicates, missing edges, low-confidence + high-contention points
→ Agent investigates: "this claim has 0.45 confidence_mean but 0.62 contention — 8 impl_count, 13 nand_count, 21 total — strong disagreement, not just uncertainty"
→ Agent creates mitigating evidence or corrects the weak claim
```

### Journey B: Delegated Agent (Best-Match Mode)
```
Agent: "Before implementing pricing, check what the graph believes about pricing models"
→ tortoise_search("pricing model for hosted platform", min_confidence=0.0)  // default: no filter
→ RRF fusion: FTS + vector + structural → ranked results
→ Each result: match_source, scores (fts/vector/structural/rrf), EP breakdown
→ Agent reads top-5 results, incorporates into decision
→ Agent: "graph says $9-99/mo per customer, free tier gives core CRUD — proceeding"
```

### Edge Cases
- No results: empty list returned, no exception
- Indexes missing: degradation chain activated, logged
- Embedding model absent: skip embeddings, fall back to FTS + structural
- Embedding model download on first use: lazy-loaded singleton, first `compute_embedding()` call blocks briefly

---

## 2. Workflows (Query Pipeline)

```
tortoise_fts_query(query, kind=None, context=None, *, 
                   min_confidence=0.0, order_by="relevance", limit=10, threshold=0.0)
│
├─ 1. CLASSIFY (3-tier — routes to strategies, not vocabulary tiers)
│   ├─ query is None AND context is set → FULL-SCAN (skip RRF, structural only, return all; kind is post-filter if provided)
│   ├─ kind is set AND query is None AND context is None → structural only (exact kind/context match)
│   └─ query string present → BEST-MATCH (activate all available: FTS + vector + structural)
│
├─ 2. RETRIEVE + DEGRADE (parallel, per-strategy)
│   ├─ Each strategy runs with timeout; on failure: skip, log, continue
│   ├─ _run_fts_query(query, limit*2) → FalkorDB FTS index → [(id, score), ...]
│   ├─ _run_vector_query(query_vec, limit*2) → FalkorDB vector index → [(id, score), ...]
│   └─ _run_structural_query(kind, context, limit*2) → range index → [(id, score), ...]
│
├─ 3. FUSE (RRF, skipped if full-scan mode or only 1 strategy active)
│       └─ _fallback_tfidf is a private helper of _degradation_chain(), not an independent entry point
│
├─ 5. ANNOTATE (post-retrieval EP enrichment, batch query)
│   └─ _annotate_ep_batch(result_ids) → single Cypher query fetching EP data for all result IDs at once
│       └─ Returns: {id: {confidence_mean, impl_count, nand_count, total, contention}}
│       └─ Flat Cypher response is wrapped into EpBreakdown dataclass (with nested EpEvidence) — see §3 Data Model
│
└─ 6. FILTER + ORDER + RETURN
    ├─ Apply threshold filter — minimum RRF score (default 0.0 = no filter)
    ├─ Apply min_confidence filter (default 0.0 = no filter)
    ├─ Sort by order_by ("relevance" = RRF score, "confidence" = confidence_mean desc)
    └─ Return [{id, content, scores: {fts, vector, structural, rrf}, match_source,
                ep: {confidence_mean, evidence: {impl_count, nand_count, total}, contention}}]
```

### Failure Modes
| Failure | Behavior |
|---------|----------|
| FTS timeout (>500ms) | Log warning, skip FTS, continue with remaining strategies |
| Vector timeout (>500ms) | Log warning, skip vector, continue with remaining strategies |
| One parallel query hangs | All queries have timeout wrapper; hanging query skipped after timeout |
| Docker FalkorDB connection drop | Catch connection error, degrade to remaining strategies or TF-IDF |
| Vector index corrupted | Catch exception, skip vector, log error |
| Malformed Cypher response | Catch parse error, skip that strategy, log error |
| Embedding model OOM | Skip embedding creation entirely (graceful), log warning |
| Embedding model download fails | Lazy-load with timeout; if unavailable, all embeddings = None |
| All strategies fail | Fallback to in-memory TF-IDF (loads all Points — O(n) memory, may OOM on large graphs) |
| Empty query string | Return empty list (don't query FalkorDB with empty string) |
| Invalid limit (<1) | Raise ValueError |
| Invalid threshold (<0 or >1) | Raise ValueError |
| EP annotation query fails | Return results without EP breakdown (ep = None), log warning |

---

## 3. Data Model

### New Point Property: `embedding`
```python
# Added to Point node on create_point() / ingest() / PointRevised
{
  "embedding": [0.0123, -0.0456, ...],  # list[float], 384-dim, nullable
}
```
- Created/updated when sentence-transformers is available AND content changes
- `None` when model not installed (graceful degradation)
- **PointRevised must re-compute embedding** — revised points with stale embeddings break vector search

### Search Result Schema
```python
@dataclass
class SearchResult:
    id: str
    content: str
    point_kind: str
    context: str | None
    
    # RRF scores (per-strategy + combined)
    scores: SearchScores
    
    # Which strategy produced the primary match
    match_source: Literal["fts", "vector", "structural", "rrf", "tfidf"]
    
    # EP breakdown (post-retrieval annotation)
    ep: EpBreakdown | None  # None if EP not computed or annotation query failed

@dataclass
class SearchScores:
    fts: float | None        # None if FTS strategy not active
    vector: float | None     # None if vector strategy not active
    structural: float | None # None if structural strategy not active
    rrf: float               # Always present (combined RRF score, or single-strategy score if only 1 active)

@dataclass
class EpBreakdown:
    confidence_mean: float       # Beta posterior mean, 0.0-1.0
    evidence: EpEvidence
    contention: float            # nand_count / total, 0.0-1.0 (0.0 when total=0)

@dataclass
class EpEvidence:
    impl_count: int    # Number of IMPL (supporting) edges
    nand_count: int    # Number of NAND (contradicting) edges
    total: int         # impl_count + nand_count
```

### `search()` — Removed

`search()` is removed. Legacy code archived at `tortoise/archived/search_legacy.py`.

### Indexes (created in `_ensure_indexes()`)
```python
# Range (existing — unchanged)
CREATE INDEX FOR (n:Point) ON (n.id)
CREATE INDEX FOR (n:Point) ON (n.pointKind)
CREATE INDEX FOR (n:Point) ON (n.context)
CREATE INDEX FOR (n:Point) ON (n.content_hash)
CREATE INDEX FOR (n:Point) ON (n.is_operator)

# Full-text (NEW — idempotent try/except, same pattern as range indexes)
CALL db.idx.fulltext.createNodeIndex('Point', 'content')

# Vector (NEW — idempotent try/except, 384-dim HNSW)
CALL db.idx.vector.createNodeIndex('Point', 'embedding', 384, 'HNSW')
```

---

## 4. Architecture

### Component Diagram
```
┌──────────────────────────────────────────────────────────┐
│ MCP Server (mcp_server.py)                               │
│  tortoise_search(query, kind?, context?, threshold?,     │
│                  limit?, min_confidence?, order_by?)     │
│  tortoise_query(kind?, context?, text?, order_by?,       │
│                 min_confidence?, limit?)                 │
│  tortoise_suggest_entry_points(query, kind_filter?)      │
└───────────────────────┬──────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│ SDK (sdk.py)                                             │
│  tortoise_fts_query()  ← NEW public API                  │
│  tortoise_query()      ← UPDATED: +text, +order_by,      │
│                           +min_confidence                 │
│  (search() removed — archived)                          │
└───────────────────────┬──────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│ Search Engine (search_engine.py)  ← NEW module           │
│  _classify_query()       query → strategy activation     │
│  _run_fts_query()        FalkorDB FTS                    │
│  _run_vector_query()     FalkorDB vector ANN             │
│  _run_structural()       range index filter              │
│  _rrf_fusion()           Reciprocal Rank Fusion (k=60)   │
│  _degradation_chain()    per-strategy fallback            │
│  _annotate_ep_batch()    batch EP query (single Cypher)  │
└───────────────────────┬──────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│ FalkorDB (Docker/server mode)                            │
│  FTS Index ── Vector Index (HNSW) ── Range Indexes       │
└──────────────────────────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│ Embeddings (embeddings.py) ← UPDATED                     │
│  compute_embedding()    pre-compute at create/ingest/     │
│                          PointRevised                     │
│  EmbeddingModel         lazy-loaded singleton             │
│  search_points()        TF-IDF fallback (existing)        │
└──────────────────────────────────────────────────────────┘
```

### New Module: `tortoise/search_engine.py`
All search logic in dedicated module. SDK is ~62K lines — adding ~600 lines of search logic there is irresponsible. Extraction cost is zero: all functions are net-new, nothing is moved.

### Modified Files
| File | Change |
|------|--------|
| `tortoise/sdk.py` | Add `tortoise_fts_query()`, remove `search()` (archive to `tortoise/archived/search_legacy.py`), update `tortoise_query()` |
| `tortoise/search_engine.py` | **NEW** (~600 lines) — all search logic |
| `tortoise/embeddings.py` | Add `compute_embedding()` + `EmbeddingModel` lazy singleton, wire into `create_point()`, `ingest()`, `PointRevised` |
| `tortoise/projection/__init__.py` | Extend `_ensure_indexes()` with FTS + vector, add FalkorDB version check |
| `tortoise/mcp_server.py` | Wire `tortoise_search` → `tortoise_fts_query()`, add `min_confidence`/`order_by` params to MCP tools |
| `skills/how-to-use-tortoise/SKILL.md` | Add search section (two modes, tool selection, EP breakdown fields) |
| `tests/test_tortoise_search.py` | Extend with hybrid search tests (see Test Footprint below) |

### Test Footprint
| Layer | Count | Notes |
|-------|-------|-------|
| Unit (pytest) | ~14 tests | RRF fusion, 3-tier classifier, order_by/min_confidence params, degradation logic, removed |
| Integration (pytest + real FalkorDB) | ~20 tests | FTS/vector/range queries, embedding pipeline, SDK API contract, MCP tool output shape, E2E-1 through E2E-7 |
| E2E (MCP client) | ~3 tests | E2E-8 (MCP search flow), full-scan clickthrough, confidence-filtered clickthrough |
| Manual review | 2 tests | Agent skill review (E2E-9), benchmark report (#7700) |
| **Total** | **~39 tests** | ~600 lines of test code in addition to ~600 lines of implementation |

---

## 5. Interfaces (API Contracts)

### `tortoise_fts_query()` — New Public API
```python
def tortoise_fts_query(
    self,
    query: str | None = None,
    kind: str | None = None,
    context: str | None = None,
    *,
    min_confidence: float = 0.0,
    order_by: Literal["relevance", "confidence"] = "relevance",
    limit: int = 10,
    threshold: float = 0.0,
) -> list[SearchResult]:
    """Hybrid search with RRF fusion + EP annotation.
    
    Full-scan mode: omit query, set context → all Points in context (no RRF, no confidence filter).
    Structural mode: set kind, omit query → exact kind/context match, no RRF fusion.
    Best-match mode: provide query → RRF fusion of FTS + vector + structural.
    
    All results annotated with EP breakdown (confidence_mean + evidence + contention).
    threshold: minimum RRF score (default 0.0 = no filter).
    min_confidence: minimum confidence_mean (default 0.0 = no filter).
    """
```

### `tortoise_query()` — Updated
```python
def tortoise_query(
    self,
    kind: str | None = None,
    context: str | None = None,
    *,
    text: str | None = None,                  # NEW: routes to fts_query when present
    order_by: str | None = None,              # NEW: "confidence" or None (natural order)
    min_confidence: float | None = None,       # NEW: optional filter (default 0.0 when text present)
    limit: int = 100,
) -> list[dict]:
    """Query Points with optional hybrid text search.
    
    When text is provided, delegates to tortoise_fts_query() for hybrid search.
    When text is None, uses existing structural query (full-scan for context, filtered for kind).
    """
```

### `search()` — Removed

`search()` is removed. Legacy code archived at `tortoise/archived/search_legacy.py`.

**Migration:** `sdk.search(q, kind=k, context=c)` → `sdk.tortoise_fts_query(q, kind=k, context=c)`

### MCP Tool Signatures
```
tortoise_search(query, kind?, context?, threshold?, limit?,
                min_confidence?, order_by?)
  → delegates to sdk.tortoise_fts_query()
  → returns SearchResult[] serialized via asdict() with full EP breakdown

tortoise_query(kind?, context?, text?, order_by?, min_confidence?, limit?)
  → routes to tortoise_fts_query() when text param present
  → returns dict[] with ep_breakdown annotation

tortoise_suggest_entry_points(query, kind_filter?)
  → uses tortoise_fts_query() for semantic entity resolution
```

### MCP Serialization (SearchResult → JSON)
```python
def _serialize_search_results(results: list[SearchResult]) -> list[dict]:
    """Convert SearchResult dataclasses to JSON-safe dicts for MCP response."""
    return [dataclasses.asdict(r) for r in results]
```

---

## 6. Implementation Steps (with Parallelism)

### Phase 0: Foundation — MVP (~600 lines impl + ~400 lines tests, 1 PR)
> **MVP CHECKPOINT:** Phase 0 delivers FTS + structural search with RRF fusion + EP annotation + degradation chain. Independently shippable — no new dependencies (no sentence-transformers needed). Vector search is implemented but returns no results until embeddings exist (Phase 1).

| Step | File | What | Parallel? |
|------|------|------|-----------|
| 0.1 | `projection/__init__.py` | Extend `_ensure_indexes()`: add FTS + vector index creation, idempotent try/except. Add FalkorDB version check: log warning if version < minimum required for FTS/vector. | — |
| 0.2 | `search_engine.py` (NEW) | `_classify_query()`: query-is-None + context → full-scan; kind + no-text → structural; text present → best-match (activate all: FTS + vector + structural) | — |
| 0.3 | `search_engine.py` | `_run_fts_query(query, limit)`: FalkorDB FTS via Cypher CALL, with timeout wrapper | — |
| 0.4 | `search_engine.py` | `_run_vector_query(query_vec, limit)`: FalkorDB vector ANN via Cypher CALL, with timeout wrapper | — |
| 0.5 | `search_engine.py` | `_run_structural_query(kind, context, limit)`: range index via MATCH WHERE | — |
| 0.6 | `search_engine.py` | `_rrf_fusion(ranked_lists, k=60)`: pure Python RRF, handles empty lists | — |
| 0.7 | `search_engine.py` | `_degradation_chain()`: orchestrates per-strategy fallback, logs warnings at each skip | — |
| 0.8 | `search_engine.py` | `_annotate_ep_batch(result_ids)`: single Cypher query fetching EP data (impl_count, nand_count, confidence_mean) for ALL result IDs at once. Computes contention = nand_count/total. | — |
| 0.9 | `sdk.py` | `tortoise_fts_query()`: public API wiring all of the above. Also update `tortoise_query()` to route `text` param through `tortoise_fts_query()`. | — |
| 0.10 | `sdk.py` | Remove `search()`. Archive to `tortoise/archived/search_legacy.py`. Update call sites to `tortoise_fts_query()`. | — |

### Phase 1: Embeddings + MCP Wiring (parallel streams)

**Stream A — Embeddings (#7698):** Depends on Phase 0 (shares `sdk.py` and `projection/__init__.py` — merge conflict risk if parallel). Logically independent of search engine but serialized by file overlap. Implement after Phase 0 merges.
| Step | File | What |
|------|------|------|
| 1.1a | `embeddings.py` | `EmbeddingModel` lazy singleton: loads `all-MiniLM-L6-v2` on first use, caches, handles download failure gracefully |
| 1.1b | `embeddings.py` | `compute_embedding(content)`: truncate to 512 tokens, encode, return list[float] or None |
| 1.1c | `sdk.py` | Wire `compute_embedding()` into `create_point()` and `ingest()` — store on Point node |
| 1.1d | `projection/__init__.py` | Wire `compute_embedding()` into `PointRevised` — re-compute when content changes |

**Stream B — MCP Wiring:** Depends on Phase 0 (needs `tortoise_fts_query()` to exist).
| Step | File | What |
|------|------|------|
| 1.2 | `mcp_server.py` | Wire `tortoise_search` → `sdk.tortoise_fts_query()`, add `min_confidence` + `order_by` params, serialize via `asdict()` |
| 1.3 | `mcp_server.py` | Update `tortoise_query` to route `text` param through `tortoise_fts_query()` |
| 1.4 | `mcp_server.py` | Update `tortoise_suggest_entry_points` to use `tortoise_fts_query()` for semantic entity resolution |
| 1.5 | `mcp_server.py` | EP breakdown in MCP response: ensure `_serialize_search_results()` passes through `ep` field |

### Phase 2: Confidence + Skill (parallel streams, 1 PR)

All three steps are independent — can run in parallel.
| Step | Issue | What |
|------|-------|------|
| 2.1 | #7699 | `sdk.py`: add `order_by` + `min_confidence` to `tortoise_query()` (text path maps to `tortoise_fts_query` params) |
| 2.2 | #7701 | `search_engine.py`: EP annotation already in Phase 0 with full breakdown. Phase 2 adds any missing edge cases (e.g., EP recompute trigger, confidence staleness handling) |
| 2.3 | — | `skills/how-to-use-tortoise/SKILL.md`: add search section (two modes: full-scan vs best-match, when to use each MCP tool, `order_by`/`min_confidence` semantics, EP breakdown fields) |

### Phase 3: Optional (deferred)
| Step | Issue | What |
|------|-------|------|
| 3.1 | #7702 | Cross-encoder reranking — only if Phase 0 latency budget allows. Feature-flagged, default off. |

### Deferred
| Step | Original Phase | Why Deferred |
|------|---------------|-------------|
| Benchmark suite (#7700) | Phase 1 | Deferred to post-MVP. Ad-hoc `timeit` checks sufficient during Phase 0/1 development. Formal benchmarks can run as follow-up. |
| `tortoise_query` non-text path `order_by`/`min_confidence` | Phase 2 | Full-scan mode already has EP annotation; structural filtering by confidence is a separate feature from hybrid search |

---

## 7. Detailed E2E Test Specifications

### E2E-1: Point Creation Stores Embedding
**Setup:** sentence-transformers installed, FalkorDB running  
**Steps:**
1. `create_point("statement", "quantum mechanics and wave functions")`
2. Query the created Point by ID
3. Assert: `embedding` is `list[float]`, length 384, non-null, not all zeros
4. `create_point("statement", "another point")` with sentence-transformers not installed
5. Assert: `embedding` is `None` (graceful degradation)
6. `PointRevised` with new content → assert embedding is re-computed (not stale)
**Layers:** Integration (pytest + real FalkorDB)

### E2E-2: FTS Query Returns Keyword-Ranked Results
**Setup:** Points: "quantum computing", "quantum gravity", "cookie recipes"  
**Steps:**
1. `tortoise_fts_query("quantum physics")`
2. Assert: ≥2 results returned
3. Assert: "quantum computing" and "quantum gravity" rank above "cookie recipes"
4. Assert: each result has `scores.fts` (not None)
**Layers:** Integration

### E2E-3: Vector Query Returns Semantically Similar Results
**Setup:** Points with embeddings: "neural networks", "deep learning architectures", "cookie recipes", "car maintenance"  
**Steps:**
1. `tortoise_fts_query("machine learning")`
2. Assert: "neural networks" and "deep learning architectures" rank above "cookie recipes" and "car maintenance"
3. Assert: at least 1 result if semantically similar content exists
4. Assert: each result has `scores.vector` (not None)
**Layers:** Integration

### E2E-4: Hybrid RRF Fuses Keyword + Semantic
**Setup:** Points: "quantum computing breakthrough" (dual match), "wave function collapse" (semantic only), "quantum of solace" (keyword only)  
**Steps:**
1. `tortoise_fts_query("quantum physics")`
2. Assert: "quantum computing breakthrough" ranks highest (dual match → highest RRF)
3. Assert: "wave function collapse" and "quantum of solace" appear lower
4. Assert: `match_source` = "rrf" (fusion active, >1 strategy contributed)
**Layers:** Integration

### E2E-5: Graceful Degradation
**Setup:** FalkorDB with indexes, then drop vector index  
**Steps:**
1. Drop vector index → `tortoise_fts_query("test")` → success, no exception, FTS + structural fusion (vector skipped)
2. Drop all indexes → `tortoise_fts_query("test")` → in-memory TF-IDF fallback, results returned (may be empty)
3. Re-create indexes → `tortoise_fts_query("test")` → full RRF fusion restored
**Layers:** Integration

### E2E-6: Full-Scan Structural Query (No Confidence Filter)
**Setup:** Subgraph with Points at confidence 0.1, 0.5, 0.95  
**Steps:**
1. `tortoise_query(context="licensing-decision")` — no text, no min_confidence
2. Assert: ALL Points returned, including 0.1 confidence
3. Assert: each result has EP breakdown (confidence_mean, impl_count, nand_count, total, contention)
4. Assert: `match_source` = "structural" (RRF was NOT invoked — full-scan path)
5. Assert: ALL Points in context returned (count matches known total — completeness verified, no hidden limit truncation)
6. Assert: two 0.50-mean Points are distinguishable: one has total=3 (uncertainty), one has total=19 + contention=0.58 (disagreement)
**Layers:** Integration

### E2E-7: Confidence-Breakdown Hybrid Search
**Setup:** Points with diverse EP states (high confidence, high contention, low evidence)  
**Steps:**
1. `tortoise_fts_query("pricing model")` → RRF-ranked
2. Assert: EP breakdown on every result (confidence_mean, evidence, contention)
3. Find Point with 0.50 mean + low total evidence → assert: `evidence.total < 5`
4. Find Point with 0.50 mean + high contention → assert: `contention > 0.3`, evidence.total > 10
5. `tortoise_fts_query("pricing model", min_confidence=0.6)` → only ≥0.6 returned
6. `tortoise_fts_query("pricing model", order_by="confidence")` → sorted by confidence_mean desc
7. SDK-level: `tortoise_fts_query` handles both. MCP-level: `tortoise_search` with `min_confidence` and `order_by` params. Steps 5-6 test SDK; MCP tool adds param passthrough in E2E-8.
**Layers:** Integration (steps 1-6), E2E (step 7 — MCP)

### E2E-8: MCP tortoise_search Uses Hybrid Search
**Setup:** FalkorDB with populated indexes, MCP server running  
**Steps:**
1. `mcp__tortoise__tortoise_search` with query "quantum mechanics"
2. Assert: `match_source` not "tfidf" (NOT in-memory TF-IDF path)
3. Assert: `scores` (fts, vector, structural, rrf) + `ep` (confidence_mean, evidence, contention) in result
4. Assert: response latency measured and recorded (target < 300ms; benchmark determines achievability — E2E-8 records, doesn't block)
5. `tortoise_search` with no query + context → full-scan results
6. `tortoise_search` with `min_confidence=0.7` → param passes through to SDK
**Layers:** E2E (MCP client or manual clickthrough)

### E2E-9: Agent Skill Teaches Search Modes
**Setup:** `skills/how-to-use-tortoise/SKILL.md` exists  
**Steps:**
1. Read the skill file
2. Assert: search section exists with two-mode design explanation
3. Assert: describes when to use `tortoise_search` (best-match) vs `tortoise_query` (full-scan) vs `tortoise_suggest_entry_points` (entity resolution)
4. Assert: explains `order_by` (relevance/confidence), `min_confidence` (default 0.0), and EP breakdown fields (confidence_mean, evidence, contention)
5. Dispatch a test agent with this skill → ask a search question → agent selects correct tool and mode
**Layers:** Manual review + agent dispatch test

---

## 8. Risk Analysis

| Risk | Severity | Mitigation |
|------|----------|-----------|
| **Embedded mode (redislite) doesn't support FTS/vector** | **High** | Check FalkorDB backend type at startup (`isinstance(db, falkordb.FalkorDB)` vs redislite). If embedded: skip FTS/vector index creation, log warning, degrade to TF-IDF-only at query time. |
| **PointRevised does not re-compute embedding → stale vector search** | **High** | Wire `compute_embedding()` into `PointRevised` projection handler. Add integration test: revise Point content → assert embedding changed. |
| **Cross-encoder pushes latency > 500ms** | **High** | Feature-flagged, default off. Only enable after post-Phase 0 ad-hoc checks confirm latency budget (formal benchmarks tracked at #7700). |
| **Sentence-transformers model download blocks first use** | **Medium** | `EmbeddingModel` lazy singleton with timeout. If download fails or exceeds 10s: log warning, set model=None, all embeddings gracefully return None. Pre-bake model in Docker image for hosted deployment. |
| **FalkorDB version too old for FTS/vector CALL** | **Medium** | Add version check in `_ensure_indexes()`: query FalkorDB version, compare against minimum (TBD from testing). If too old: skip FTS/vector index creation, log warning with upgrade instructions. |
| **Index creation at startup blocks API readiness** | **Medium** | FTS/vector index creation is O(1) on re-creation (immediate "already indexed" error). On first creation with large graph: may take seconds. Acceptable for initial deployment; consider async for future if startup time > 5s. |
| **EP annotation causes N+1 queries** | **Medium** | `_annotate_ep_batch()` fetches EP data for ALL result IDs in a single Cypher query, not one per result. Batch query: `MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH ... RETURN n.id, count(impl), count(nand), confidence`. |
| **In-memory TF-IDF fallback loads all Points → OOM on large graphs** | **Medium** | TF-IDF fallback is last resort (all indexes missing). Add LIMIT to `sdk.query()` in degradation path to cap memory usage. Log warning with graph size when fallback activates. |
| **EP-confidence-weighted RRF hypothesis is unvalidated** | **Medium** | EP is annotation-only (post-retrieval), not a pre-filter or RRF modifier. Phase 2 adds `order_by="confidence"` as a sort option — does not modify RRF formula. If confidence sort degrades relevance, agents can use default `order_by="relevance"`. No wasted Phase 2 effort. |
| **MCP SearchResult serialization failure** | **Medium** | Use `dataclasses.asdict()` for JSON-safe dict conversion. Add integration test: serialize SearchResult → dict → JSON → assert all fields present, no `Decimal` or non-serializable types. |
| **FalkorDB FTS/vector APIs require raw Cypher CALL** | **Medium** | Test directly against falkordb-py; build wrapper functions in search_engine.py that abstract CALL vs native API. If native API available later, swap wrapper internals without changing callers. |
| **RRF fusion increases latency above target** | **Medium** | Parallel queries with timeout wrapper (500ms each). Degrade to single-strategy if combined latency exceeds budget. E2E-8 measures latency; target is 300ms, actual achievability determined by #7700 benchmark. |
| **Agent skill update not read or used by agents** | **Medium** | Test by dispatching agent with updated skill → ask search question → verify agent selects correct MCP tool. If agents don't read skill, surface as discovery problem (separate from this epic). |
| **Existing callers of `search()` break** | **Low** | `search()` removed. Only 2 call sites (MCP server, tests). Update to `tortoise_fts_query()`. Legacy code archived. |
| **Embedding model OOM on large text** | **Low** | Truncate content to 512 tokens before encoding. |
| **EP contention = NaN when total=0** | **Low** | Default contention = 0.0 when no edges exist. |
| **Network error / rate limit on FalkorDB** | **Low** | Connection errors caught in per-strategy timeout wrapper → degrade to remaining strategies. Rate limiting unlikely at current scale; add retry with backoff if observed. |

---

## Implementation Dependency Graph
```
                    ┌─────────────────────────────────┐
                    │ Phase 0: Foundation (MVP)        │
                    │ 0.1 → 0.2 → 0.3-0.5 (parallel) │
                    │         ↓                        │
                    │  0.6 → 0.7 → 0.8 → 0.9 → 0.10  │
                    └─────────────┬───────────────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
┌─────────▼──────────┐  ┌────────▼─────────┐             │
│ Stream A (parallel) │  │ Stream B          │             │
│ 1.1a → 1.1b → 1.1c │  │ 1.2 → 1.3 → 1.4   │             │
│ → 1.1d              │  │ → 1.5              │             │
│ Embeddings (#7698)  │  │ MCP Wiring         │             │
└─────────┬──────────┘  └────────┬─────────┘             │
          │                       │                       │
          └───────────────────────┼───────────────────────┘
                                  │
                    ┌─────────────▼───────────────────┐
                    │ Phase 2 (all parallel)           │
                    │ 2.1 (#7699) | 2.2 (#7701) | 2.3 │
                    └─────────────┬───────────────────┘
                                  │
                    ┌─────────────▼───────────────────┐
                    │ Phase 3 (optional, deferred)     │
                    │ 3.1 (#7702)                      │
                    └─────────────────────────────────┘

Deferred: Benchmark suite (#7700) — post-MVP follow-up
```
