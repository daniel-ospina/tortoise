# Research Summary: Tortoise Hybrid Search & Retrieval

**Date:** 2026-08-03
**Reframed Problem:** Tortoise agents trying to find relevant Points from NL queries but TF-IDF alone produces poor precision on short text — resulting in missed context and degraded agent decisions.

---

## Source Confidence Summary

| Claim | Tier | Sources |
|-------|------|---------|
| FalkorDB supports full-text indexes via RediSearch integration | **Medium** | ⚠️ emerging — confirmed in docs but `falkordb-py` API surface unverified; may require raw Cypher CALL |
| FalkorDB supports vector indexes with HNSW | **Medium** | ⚠️ emerging — confirmed in docs but API + similarity score return path unverified |
| RRF formula: score = sum(1/(k + rank_i)), k=60 | **High** | Standard IR literature (Cormack et al. 2009), verified by multiple independent implementations |
| sentence-transformers works as embedding pipeline (already imported) | **High** | Codebase `embeddings.py` already uses `all-MiniLM-L6-v2` |
| Cross-encoder reranking is ~100x slower than bi-encoder but more accurate | **Medium** | ⚠️ single-source — common IR knowledge, verify latency with target model |
| EP confidence can be incorporated as RRF weight multiplier | **Low** | ⚠️ hypothesis — novel integration, no prior art; requires empirical validation |

> **⚠️ Research limitations:** `web_search`/Perplexity unavailable (API key not set). Findings are based on codebase analysis, FalkorDB docs, and domain knowledge. External claims marked Medium or Low may upgrade with web research.

### Deployment Mode Gap

**🔴 `redislite` (embedded mode) likely does NOT support FTS/vector indexes.** RediSearch and vector index modules are Redis server modules that must be loaded by the Redis process. `redislite` is a Python-native Redis implementation — it almost certainly does not load these modules. FalkorDB-on-Docker likely does include them (FalkorDB ships with RediSearch).

**Impact:** The architecture must handle mode-dependent capability detection:
- Docker/server FalkorDB: FTS + vector indexes available → full RRF fusion
- Embedded (redislite): FTS/vector likely unavailable → in-memory TF-IDF only
- The graceful degradation chain must detect mode at startup, not at query time

**Verification required:** Test `CALL db.idx.fulltext.createNodeIndex` and `CALL db.idx.vector.createNodeIndex` against both `falkordb` (Docker) and `redislite.falkordb_client` (embedded).

## Contradictions / Tensions

| Tension | Detail |
|---------|--------|
| FTS confidence vs API uncertainty | FalkorDB docs confirm FTS/vector indexes exist, but `falkordb-py` API surface for `CALL db.idx.*.createNodeIndex` is untested. If raw Cypher CALL is needed, integration is straightforward but not "native Python." |
| Parallel RRF vs sequential fallback | The doc describes two incompatible architectures. See reconciled design below. |
| 3-tier hierarchy vs RRF fusion | Hierarchy routes queries to a single index; RRF runs all in parallel. Hierarchy is a pre-filter, RRF is the fusion engine — not alternatives. |

---

## What We Have Internally

### Existing search infrastructure
- `tortoise/embeddings.py`: `search_points()` — in-memory TF-IDF/sentence-transformers search over Points dict. Not integrated with FalkorDB. Uses `all-MiniLM-L6-v2` (384-dim).
- `tortoise/sdk.py`: `search()` — wraps `search_points()`, loads ALL Points into memory via `self.query()`. No FalkorDB FTS/vector utilization.
- `tortoise/projection/__init__.py`: `_ensure_indexes()` — creates range indexes (id, pointKind, content_hash, is_operator [non-embedded FalkorDB docker/server only — embedded drops it: falkordblite bool type-table degradation across reopen, so an indexed `= false` silently returns 0; see #522/#1069]) with try/except idempotency. Same pattern extends to FTS/vector.

### Gaps
- **No FalkorDB index utilization in search**: All search is in-memory Python.
- **No FTS or vector indexes created**: Only range indexes exist.
- **No embedding storage**: Points don't store embedding vectors.
- **No RRF fusion**: Single-strategy retrieval (TF-IDF only).
- **No confidence filtering**: `tortoise_query()` doesn't support order_by/min_confidence.

---

## External Findings

### 1. FalkorDB Indexing

FalkorDB supports three index types:

| Index Type | Syntax | Use Case |
|-----------|--------|---------|
| Range | `CREATE INDEX FOR (n:Point) ON (n.prop)` | Exact match — already used |
| Full-text | `CALL db.idx.fulltext.createNodeIndex('Point', 'content')` | Free-text with stemming, scoring |
| Vector | `CALL db.idx.vector.createNodeIndex('Point', 'embedding', 384, 'HNSW')` | ANN similarity via HNSW |

Vector dimension must match — `all-MiniLM-L6-v2` produces 384-dim vectors. Consistent across all Points.

### 2. RRF (Reciprocal Rank Fusion)

Formula: `RRF(d) = sum(1/(k + rank_i(d)))` where k=60 (default).

Key properties: score-free fusion (ranks only, no calibration needed), robust to outliers, k controls decay rate.

### 3. Embedding Pipeline

Pre-compute at `create_point()` / ingest time. Store as `embedding: list[float]` property on Point node. Same model must be used consistently. FalkorDB vector index enables fast ANN search.

### 4. Cross-Encoder Reranking (Phase 3, optional)

Two-stage: bi-encoder retrieves top-K (100-200) → cross-encoder re-ranks. ~100x slower but higher accuracy. Risk: may push above 300ms latency target.

### 5. EP-Weighted Ranking (Phase 2)

Formula: `final_score = RRF(d) * (0.5 + 0.5 * confidence_mean)`

EP modulates by at most 50% (0.5x-1.0x range). Open question: does EP confidence correlate with retrieval relevance?

---

## 3-Tier Query Hierarchy

| Tier | Query Mode | Index Used |
|------|-----------|-----------|
| **Kind** | Exact match on `pointKind` + `context` | Range index |
| **Abstract** | Full-text search on `content` | Full-text index |
| **Core-type** | Vector similarity on `embedding` | Vector index (HNSW) |

---

## Architecture Recommendation

**Primary path: RRF fusion of all available strategies.** The 3-tier hierarchy acts as a pre-filter determining which indexes are available for a given query (e.g., kind=statement → structural index always included; NL query → FTS + vector included). RRF fuses all available strategies into a single ranking.

**Query flow:**
1. **Pre-filter (3-tier hierarchy):** Query classifies as kind-match / abstract-text / core-semantic → determines which strategies to activate
2. **Parallel retrieval:** All active strategies run simultaneously against FalkorDB indexes
3. **RRF fusion:** Ranked lists combined via `sum(1/(60 + rank_i))`
4. **Degradation:** If a strategy fails (index missing, timeout), fall back to remaining strategies. If ALL FalkorDB strategies fail → in-memory TF-IDF. Guaranteed to return results.

```
tortoise_fts_query(query, kind=None)
  ├─ Pre-filter: classify query → activate FTS / vector / structural
  ├─ Parallel: _run_fts_query() | _run_vector_query() | _run_structural_query()
  ├─ Fuse: _rrf_fusion(ranked_lists)
  └─ Fallback: if all fail → search_points() (in-memory TF-IDF)
```

**Graceful degradation chain (per-strategy):**
FTS index missing → skip FTS, fuse vector + structural only
Vector index missing → skip vector, fuse FTS + structural only
All indexes missing → in-memory TF-IDF fallback

### Implementation Order

**Phase 0 (Foundation):** ~600 lines across 6 functions (estimate based on prior implementation attempt in deleted worktree — no commit available, rebuild from scratch):
1. Create FTS + vector indexes in `_ensure_indexes()` (extend existing range-index pattern)
2. Implement `_run_fts_query()`, `_run_vector_query()`, `_run_structural_query()`
3. Implement `_rrf_fusion()` engine
4. Implement `tortoise_fts_query()` public API with degradation chain
5. Wire 3-tier query classifier (kind → abstract → core-type dispatch)

**Phase 1 (#7698, #7700):** Embedding pipeline + benchmark
**Phase 2 (#7699, #7701):** Confidence filtering + EP-weighted RRF
**Phase 3 (#7702, optional):** Cross-encoder reranking

### Open Questions
- Does FalkorDB `falkordb-py` expose FTS/vector index creation APIs natively?
- Can FalkorDB vector search return similarity scores?
- Does EP confidence correlate with retrieval relevance? (empirical — #7701)
