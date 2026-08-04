# Epic Scope: Tortoise Hybrid Search & Retrieval (#7697)

**Date:** 2026-08-03
**Inputs:** Align Decision (PROCEED, DO NOW) + Research Brief (`docs/research/2026-08-03-hybrid-search.md`)

---

## Scope Boundaries

### In Scope

| Item | Phase | Description |
|------|-------|-------------|
| FTS index creation | 0 | `CALL db.idx.fulltext.createNodeIndex` on `Point.content` — idempotent, extends `_ensure_indexes()` |
| Vector index creation | 0 | `CALL db.idx.vector.createNodeIndex` on `Point.embedding` (384-dim, HNSW) — idempotent |
| `_run_fts_query()` | 0 | Cypher full-text search returning scored + ranked Point IDs |
| `_run_vector_query()` | 0 | Cypher vector ANN search returning scored + ranked Point IDs |
| `_run_structural_query()` | 0 | Range index match on `pointKind` + `context` CONTAINS |
| `_rrf_fusion()` | 0 | Reciprocal Rank Fusion (k=60) combining 2-3 ranked lists |
| `tortoise_fts_query()` | 0 | Public API: 3-tier classifier → parallel retrieval → RRF fusion → degraded fallback |
| Graceful degradation | 0 | Per-strategy fallback: FTS missing → skip, vector missing → skip, all missing → in-memory TF-IDF |
| Embedding pipeline | 1 (#7698) | Pre-compute `all-MiniLM-L6-v2` (384-dim) embeddings at `create_point()` + `ingest`; store as Point property |
| **MCP `tortoise_search` → `tortoise_fts_query()`** | 1 | 🔴 Primary customer surface — wire the MCP tool to hybrid search (today: in-memory TF-IDF) |
| **MCP `tortoise_query` FTS integration** | 1 | `tortoise_query` with text param → route through `tortoise_fts_query()` when query string present |
| **Result strategy metadata** | 1 | Each result carries `match_source` (fts/vector/structural/rrf) + per-strategy scores for debuggability. Also includes EP breakdown: `confidence_mean`, `evidence` (impl_count/nand_count/total), and `contention` (nand_ratio = nand/total). This disambiguates "50% from few sources" vs "50% from strong disagreement." |
| Benchmark suite | 1 (#7700) | FalkorDB FTS vs vector vs hybrid vs TF-IDF — latency + precision/recall. **Deferred to post-MVP per plan** — ad-hoc timeit sufficient during development. |
| SDK `tortoise_query()` confidence params | 2 (#7699) | `order_by` supports `confidence` (sort by EP confidence) and `relevance` (default, via RRF). `min_confidence` is OPTIONAL — defaults to no filter (returns everything, including low-confidence points). Reasoning workflows need full visibility. |
| EP confidence annotation | 0 (#7748) + 2 (#7701) | Core EP breakdown (confidence_mean + evidence + contention) ships in Phase 0 via `_annotate_ep_batch()`. Phase 2 (#7701) adds edge cases: contention=0.0 when total=0, staleness detection, batch correctness for large sets. Post-retrieval annotation, not pre-retrieval filter. |
| **`tortoise_suggest_entry_points` FTS** | 2 | Upgrade entry-point resolution from CONTAINS to hybrid search |
| **Update `how-to-use-tortoise` skill** | 2 | Add search section: when to use each MCP tool, two-mode design, `order_by` semantics, confidence annotation. Without this, agents never discover the new capabilities. |

### Out of Scope

| Item | Reason | Deferred to |
|------|--------|-------------|
| Cross-encoder reranking | Optional Phase 3, latency risk above 300ms | #7702 (kept as optional child) |
| Embedded mode (redislite) FTS/vector | `redislite` likely doesn't load RediSearch/HNSW modules — needs verification | Future issue |
| REST API search endpoint | #7711 (Hosted Platform) scope | #7711 |
| Index observability/monitoring | Platform monitoring concern | Future issue |
| Existing Point embedding backfill | Migration script for pre-existing Points | Future issue |
| GraphRAG integration | Separate epic | Future epic |
| Legacy `search()` API removal | Removed in Phase 0 — archived to `tortoise/archived/search_legacy.py`. 2 call sites affected. | — (intentional, confirmed during planning) |

### Boundary Rationale

**Principle:** Ship the retrieval engine first, optimize ranking later. Phase 0-1 delivers working hybrid search (competitive with Neo4j/Supermemory/Honcho). Phase 2 adds Tortoise's unique differentiator (EP-weighted ranking). Phase 3 (cross-encoder) is speculative — validate latency budget first.

---

## Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| **UX** | low | SDK/CLI only — no UI changes. `tortoise_fts_query()` is a single new method. |
| **Architecture** | complex | New subsystem: 3 FalkorDB indexes, RRF fusion engine, 3-tier query classifier, degradation chain, MCP tool wiring. Multi-backend. Two distinct search modes: full-scan structural (reasoning) + best-match ranked (context). |

### Design Principle: Two Search Modes

**Based on actual Tortoise usage analysis (Aug 2, 2026):**

| Mode | What | Who Uses It | EP Confidence |
|------|------|-------------|---------------|
| **Full-scan / Structural** | "Give me everything in this subgraph" | Graph reviewers, verify-chain, integrity checks | **Never filter** — low-confidence points are exactly what reviewers need to see |
| **Best-match / Ranked** | "Give me the top N relevant points" | Delegated agents, entity resolution, context retrieval | **Annotate, don't filter** — annotate each result with EP confidence; caller decides composite |

**Rules:**
1. `min_confidence` is OPTIONAL — defaults to no filter (0.0)
2. `order_by` supports `relevance` (RRF, default) and `confidence` (EP, explicit opt-in)
3. EP confidence is always a **post-retrieval annotation**, never a pre-retrieval gate
4. Full-scan queries (context-only, no text query) return everything — RRF is skipped, only structural index used
| **Ontology** | low | No new vocabulary. Extends existing search on `Point` nodes with new indexes. |
| **Accessibility** | low | No user-facing UI. |

---

## High-Level E2E Test Cases

### E2E-1: Point creation stores embedding
**Given:** sentence-transformers is installed  
**When:** `create_point("statement", "quantum mechanics and wave functions")` is called  
**Then:** Point is created with `embedding` property (384-dim float list)  
**And:** embedding is non-null, non-empty  

### E2E-2: FTS query returns keyword-ranked results
**Given:** Points exist with content "quantum computing", "quantum gravity", "cookie recipes"  
**When:** `tortoise_fts_query("quantum physics")` is called  
**Then:** Results are ranked by full-text relevance  
**And:** "quantum computing" and "quantum gravity" rank above "cookie recipes"  

### E2E-3: Vector query returns semantically similar results
**Given:** Points with diverse semantic content exist  
**When:** `tortoise_fts_query("machine learning")` is called (with vector strategy active)  
**Then:** Results semantically related to ML (e.g., "neural networks", "deep learning") rank above unrelated content  
**And:** At least 1 result is returned if semantically similar content exists  

### E2E-4: Hybrid RRF fuses keyword + semantic rankings
**Given:** Fulltext + vector indexes both exist and are populated  
**When:** `tortoise_fts_query("quantum mechanics")` is called  
**Then:** Results reflect both keyword match AND semantic similarity via RRF fusion  
**And:** A result that matches both strongly ranks highest  
**And:** A result matching only one strategy appears lower than dual-strategy matches  

### E2E-5: Graceful degradation when indexes unavailable
**Given:** Vector index does not exist (e.g., embedded mode, or creation failed)  
**When:** `tortoise_fts_query("test query")` is called  
**Then:** Query succeeds — falls back to FTS + structural fusion (or further to in-memory TF-IDF)  
**And:** No exception is raised  
**And:** Results are returned (may be empty if no matches)  

### E2E-6: Full-scan structural query (no confidence filter)
**Given:** A subgraph has Points with EP confidence ranging from 0.1 to 0.95  
**When:** `tortoise_query(context="licensing-decision")` is called with NO min_confidence  
**Then:** ALL Points in the context are returned, including low-confidence ones  
**And:** Each result includes its EP confidence as metadata  
**And:** Low-confidence Points (0.1-0.3) are NOT filtered out — reviewer needs to see weak spots  

### E2E-7: Confidence-annotated hybrid search (optional filter)
**Given:** Hybrid search is active with EP confidences computed  
**When:** `tortoise_fts_query("pricing model", order_by="relevance")` is called  
**Then:** Results are ranked by RRF relevance score  
**And:** Each result carries EP breakdown in metadata: `confidence_mean`, `evidence` (impl_count, nand_count, total), `contention` (nand_ratio)  
**And:** Two Points with same 0.50 confidence_mean are distinguishable: one with low total evidence (uncertainty) vs one with high evidence + high nand_count (contention)  
**And:** The caller can optionally pass `min_confidence=0.5` to filter, but default is no filter  
**And:** `order_by="confidence"` sorts by EP confidence_mean descending  
**And:** `order_by="relevance"` sorts by RRF (default)  

### E2E-8: MCP tortoise_search uses hybrid search (customer surface)
**Given:** Hybrid search is deployed with FTS + vector indexes populated  
**When:** Agent calls `tortoise_search("quantum mechanics")` via MCP  
**Then:** Results are returned via `tortoise_fts_query()` hybrid RRF, NOT in-memory TF-IDF  
**And:** Each result includes `match_source` and per-strategy scores in metadata  
**And:** Latency is under 300ms for typical query load  

### E2E-9: Agent skill teaches search modes
**Given:** `how-to-use-tortoise` skill is updated with search section  
**When:** An agent reads the skill before performing a Tortoise operation  
**Then:** The skill describes when to use `tortoise_search` (best-match), `tortoise_query` (full-scan), and `tortoise_suggest_entry_points` (entity resolution)  
**And:** The skill teaches the two-mode design: full-scan returns everything (no confidence filter), best-match ranks by relevance with confidence annotation  
**And:** The skill shows `order_by` options (relevance/confidence) and when each applies  

---

**E2E test cases:** 9 drafted
**Test Design:** #7735
**Capstone Verification:** #7736
**Methodology Improvements:** #7737
**Complexity:** Architecture=complex, UX/ontology/accessibility=low

---

> ⚠️ **HUMAN GATE** — Reply "proceed" to continue to detailed planning, or give feedback on scope boundaries and E2E cases.
