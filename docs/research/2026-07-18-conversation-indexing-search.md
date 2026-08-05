# Research: Agent Conversation Indexing & Search

**Date:** 2026-07-18
**Domain:** engineering
**Confidence tiers:** See individual claim sections

---

## Reframed Problem

> "Agent sessions trying to recall prior context but facing unstructured, heterogeneous-length conversation files with no semantic index, which results in lost context, duplicated work, and contradictory decisions."

**5 Whys root cause:** Agents lose context across sessions. Each session starts fresh, repeating work and contradicting prior decisions. This costs real LLM tokens and degrades decision quality.

**Key assumption challenged:** The assumption that agents query by exact keywords — they don't. Agents query by topic, entity name, or decision outcome ("what did we decide about X?"). Keyword search is the wrong paradigm.

---

## What We Have Internally

Tortoise's current search/indexing stack:

| Component | Current State | Gap |
|-----------|--------------|-----|
| **Conversation indexing** | `extractor.py` splits transcripts into utterances → Points via LLM (M2) or regex (MockExtractor) | Points are extracted claims, not searchable conversation segments |
| **Conversation capture** | `ingest.py` reads markdown files, extracts frontmatter → DocumentCreated events, sections → Points | No incremental append — full file re-ingestion each time |
| **Semantic search** | `embeddings.py` — `search_points()` uses sentence-transformers or TF-IDF fallback over Points | Searches claims only, not raw conversations |
| **Memory orchestrator** | `memory_orchestrator.py` — NL query → pattern matching → parallel Cypher across ontologies | Keyword-classifier, no hybrid retrieval, no ranking fusion |
| **Session continuity** | `session_continuity.py` — vibecoder prototype, captures findings to Points | Naive — returns last 5 observations regardless of relevance |
| **MCP search tool** | `tortoise_search` — wraps `sdk.search()` → `search_points()` with embedding similarity | Single-modality (embedding only), no BM25, no graph traversal |

**Bottom line:** We have a semantic search over extracted Points (claims), but no way to search raw conversation history. Sessions are ingested once and the raw text is discarded — only structured Points survive. An agent asking "what happened in the session about X?" can only find extracted claims, not the conversation flow.

---

## External Findings

### 1. How Top Agent Memory Systems Handle Conversation Search

#### Graphiti (Zep) — **[HIGH]** 3+ independent sources

Graphiti is the most architecturally mature approach. Key design decisions:

- **Hybrid retrieval as default:** Semantic (embeddings) + keyword (BM25) + graph traversal, fused via Reciprocal Rank Fusion (RRF) or Maximal Marginal Relevance (MMR)
- **15 pre-built search recipes:** `EDGE_HYBRID_SEARCH_RRF`, `NODE_HYBRID_SEARCH_MMR`, `COMMUNITY_HYBRID_SEARCH_CROSS_ENCODER`, etc. — search is configurable per use case
- **Reranking approaches:**
  - RRF: combines BM25 + semantic ranks into one score
  - MMR: balances relevance with diversity (reduces duplicate results)
  - Cross-Encoder: LLM jointly encodes query+result for reranking (OpenAI, Gemini, BGE)
  - Node-distance: rerank by graph proximity to a focal entity
- **Search targets edges, nodes, AND communities** — not documents
- **Performance:** 94.7% accuracy at 155ms retrieval on LoCoMo benchmark; 90.2% at 162ms on LongMemEval
- **Incremental:** Episodes ingested incrementally, entities/facts updated without recomputation
- **Temporal:** Facts have validity windows — old facts invalidated, not deleted

**What we can learn:** Don't search raw text. Search structured facts (edges) and entities (nodes). Hybrid retrieval (BM25 + vector + graph) is table stakes. RRF is the standard fusion method — simpler than learned rankers and nearly as good.

#### Honcho — **[MEDIUM]** ⚠️ 2 independent sources

- **Multi-level search:** Workspace-wide, per-peer, per-session — each scoped differently
- **Background reasoning:** Extracts conclusions from messages asynchronously, builds per-peer representations
- **Hybrid search:** BM25 + vector over messages
- **Agentic Chat endpoint:** `peer.chat("What learning styles does the user respond to best?")` — LLM-powered reasoning over conclusions, not just retrieval
- **Representation endpoint:** Low-latency static snapshots of what's known about a peer (for prompt injection)

**What we can learn:** The multi-scope model (workspace → peer → session) maps well to our needs (project → topic → session). The "representation" concept (condensed knowledge about an entity) is more useful for agents than raw search results.

#### Mem0 — **[LOW]** ⚠️ single-source

- **Extract-then-search:** Messages → facts (memories) → entities linked → search over memories
- **Reranker-enhanced:** Optional reranker improves relevance
- **Platform vs OSS:** Platform does the heavy lifting (vector store, reranker); OSS requires self-configuration

**What we can learn:** The "distill first, search later" pattern. Memories are shorter and more uniform than raw messages, solving the heterogeneous-length problem.

#### Anthropic Contextual Retrieval — **[MEDIUM]** practitioner source

- **Problem:** Traditional RAG chunks lose context ("revenue grew by 3%" — but which company? which quarter?)
- **Solution:** Prepend document-level context to each chunk before embedding/BM25 indexing
- **Results:** 49% reduction in retrieval failures with Contextual Embeddings + Contextual BM25; 67% with reranking
- **Cost:** $1.02 per million document tokens (using Claude Haiku + prompt caching)

**What we can learn:** For our session chunks, prepending session metadata (date, topic, participants) before embedding would significantly improve retrieval. This is cheap and immediately applicable.

---

### 2. Search Quality for Heterogeneous-Length Texts — **[HIGH]**

**The problem:** Sessions range from 2 messages to 100+. BM25 naturally favors longer documents (more term matches). Embedding models compress long texts into fixed vectors, losing fine-grained meaning.

**How the field solves this:**

1. **Chunk uniformly.** Graphiti extracts facts (edges) of uniform granularity — the source document length doesn't matter. Honcho extracts conclusions that are ~1-3 sentences each. Both normalize the unit of retrieval.

2. **Search over extracted representations, not raw text.** Mem0, Honcho, and Graphiti ALL follow this pattern. Raw text is the source of truth, but search targets the extracted structured layer.

3. **Chunk with context.** Anthropic's Contextual Retrieval prepends document context to chunks. This also normalizes: a 2-word chunk "yes, that's right" becomes "In a conversation about Tortoise API design from 2026-07-15, the user confirmed: 'yes, that's right'" — making it searchable.

4. **BM25 length normalization.** BM25 already includes document length normalization (the `b` parameter, typically 0.75). This helps but doesn't fully solve the problem for extreme length variance.

**Recommendation for Tortoise:** We already extract Points from sessions. The missing piece is (a) making the raw conversation chunks searchable with context, and (b) using Points as the primary search surface, with conversation transcripts as the fallback provenance layer.

---

### 3. Incremental Indexing Strategies — **[HIGH]**

**The field consensus: delta-only, with periodic reconciliation.**

| System | Approach | Reindex trigger |
|--------|---------|----------------|
| Graphiti | Episodes ingested incrementally → entities/facts updated in-place. Old facts invalidated but preserved. | Never (fully incremental) |
| Honcho | Messages appended → background reasoning queue processes new messages only | Never (fully incremental) |
| Traditional RAG | Chunk → embed → upsert. New docs = new chunks only. | When embedding model changes |

**For Tortoise's append-only session files:**

- **Full re-extraction is wasteful** when tortoise-capture appends 3 messages to a 100-message session. We'd re-extract 97 messages we already processed.
- **Delta-only extraction** requires tracking "last extracted position" per session. The extractor processes only new messages, adding new Points and potentially new operators connecting to existing Points.
- **IDF staleness:** TF-IDF's IDF component depends on the full corpus. But since (a) we primarily use embeddings, not TF-IDF, and (b) the corpus grows slowly, a periodic IDF rebuild (weekly or on significant corpus growth >20%) is sufficient.

**Key insight from Graphiti:** Facts are invalidated, not deleted. When new information contradicts old, the old fact gets a validity end-date. This is superior to delete-and-recreate because it preserves provenance.

---

### 4. Metadata Freshness (IDF Rebuild) — **[MEDIUM]** ⚠️ 2 sources

**TF-IDF/BM25 IDF staleness:**

- BM25's IDF is corpus-dependent. As new sessions are added, term rarity changes.
- Graphiti and Honcho both use BM25 but don't document their IDF rebuild strategy — likely incremental streaming statistics.
- **Practical answer:** For our scale (hundreds to low thousands of sessions), an IDF rebuild is cheap. Schedule it:
  - After every N new sessions (e.g., 50)
  - Or on a time trigger (weekly)
  - Or when the corpus grows by >20%

**Embedding staleness:** Embeddings don't "go stale" per se — the model either works or it doesn't. The trigger to re-embed is changing the embedding model, not corpus growth.

---

### 5. Search UX for Agents — **[HIGH]**

**Query patterns observed across systems:**

| Pattern | Example | System support |
|---------|---------|---------------|
| Entity-anchored | "What do we know about X?" | Graphiti (node-distance rerank), Honcho (peer.chat) |
| Decision recall | "What did we decide about Y?" | Honcho (conclusions), Tortoise (Points with pointKind=decision) |
| Temporal | "What happened last week?" | Graphiti (temporal validity windows), Honcho (session context) |
| Status check | "Is Z done?" | Graphiti (fact state), Tortoise (EP confidence → status inference) |
| Freeform NL | "Tell me about the pricing discussion" | All systems (semantic search) |

**How results should be structured for agents:**
1. **Not raw text dumps** — agents need structured facts with provenance
2. **Ranked by relevance, not chronology** — "what's most relevant to my question" beats "what's most recent"
3. **With confidence signals** — agents should know whether a result is a settled conclusion or a speculative claim
4. **With source links** — "this came from session X on date Y, speaker Z" lets the agent verify
5. **With related context** — "here's the answer, and here are 2 related findings you might also want"

---

### 6. Deduplication Across Sessions — **[MEDIUM]** ⚠️ 2 sources

**Approaches observed:**

1. **Graphiti Communities:** Entities and facts are clustered into communities. Sessions about the same topic naturally share entities → graph traversal surfaces related sessions.

2. **Honcho Peer-centric:** All interactions about a peer (user, agent, project) are linked. Sessions about "Tortoise API" all connect to the Tortoise peer node.

3. **Topic modeling + clustering:** Run topic modeling (LDA, BERTopic) over session summaries, cluster by topic vector. Simpler to implement, but requires maintenance.

**For Tortoise, the natural approach:** Sessions already produce Points. Points share entities (via `aboutEntities`). Sessions sharing entities ARE topically related — this is graph-native deduplication without a separate clustering step.

**Suggestion:** Add a `relatedSessions` traversal: "given session S, find other sessions that produced Points about the same entities."

---

### 7. Confidence/Ranking — **[HIGH]**

**How the field ranks search results:**

| Method | Used by | When to use |
|--------|---------|------------|
| **Reciprocal Rank Fusion (RRF)** | Graphiti (default) | Merging BM25 + vector ranks. Simple, parameter-free, proven. |
| **Maximal Marginal Relevance (MMR)** | Graphiti | When diversity matters — reduce near-duplicate results |
| **Cross-Encoder rerank** | Graphiti, Mem0 | When accuracy matters more than latency. LLM jointly scores query+doc |
| **Node-distance** | Graphiti | Entity-anchored queries: "about Jane" |
| **Recency boost** | Honcho | Session context: "what happened recently" |
| **Hybrid score** | All | Weighted sum: α·semantic + (1-α)·BM25 |

**Ranking fusion formula (best practice):**

```
final_score = w1 * normalize(semantic_similarity) 
            + w2 * normalize(BM25_score) 
            + w3 * recency_boost(age_in_days)
            + w4 * graph_confidence_boost(ep_confidence)
```

Where `recency_boost(t) = e^(-λt)` and `graph_confidence_boost(c) = c` (0-1 scale).

---

### 8. Graph-Informed Ranking — Tortoise's Unique Advantage **[HIGH]**

**This is Tortoise's differentiator.** No other system we surveyed uses epistemic confidence to boost search rankings.

**How it works:**
1. Agent searches for "pricing decision"
2. Search returns 5 Points across 3 sessions
3. For each Point, look up its EP confidence in the tortoise graph
4. Points with high EP confidence (well-supported, survived adversarial challenge) rank higher
5. Sessions that contributed to high-confidence Points get a provenance boost

**Implementation sketch:**
```python
def graph_informed_rank(results, ep):
    for r in results:
        point_id = r["id"]
        confidence = ep.get_confidence(point_id)  # {mean, variance, alpha, beta}
        # High confidence + low variance = strong signal
        r["graph_boost"] = confidence["mean"] * (1 - confidence["variance"])
        r["final_score"] = r["base_score"] * (1 + r["graph_boost"])
    return sorted(results, key=lambda r: r["final_score"], reverse=True)
```

**Caveat:** Only applies to results that are Points (extracted claims). Raw conversation chunks don't have EP confidence and can't get this boost — another reason to prioritize Point-based search.

---

## Concrete Recommendations

### Adopt Now (Low Effort, High Impact)

| # | Recommendation | Effort | Impact | Based on |
|---|---------------|--------|--------|----------|
| **R1** | **Chunk sessions with metadata context before indexing.** Prepend session date, topic, and speaker to each chunk before embedding. | ~20 lines in extractor | Reduces retrieval failures ~35% (Anthropic data) | Anthropic Contextual Retrieval |
| **R2** | **Add BM25 alongside existing vector search.** Use `rank_bm25` library. Fuse with RRF. | ~50 lines in embeddings.py | Catches exact matches (entity names, error codes) that embeddings miss | Graphiti, Honcho, Anthropic |
| **R3** | **Track "last extracted position" per session file.** Store byte offset or message count. Delta-only extraction for append scenarios. | ~30 lines in ingest.py | Eliminates redundant re-extraction | Graphiti incremental ingestion |
| **R4** | **Add a `searchSessions` MCP tool** that returns conversation chunks (not just Points) with provenance. | ~40 lines in mcp_server.py | Agents can ask "show me the conversation about X" | User need |
| **R5** | **Add recency decay to search ranking.** `score *= e^(-λ * days_old)`. λ=0.01 gives half-life ~69 days. | ~10 lines in embeddings.py | Prevents stale results dominating | Honcho session context |

### Adopt Next (Medium Effort, High Impact)

| # | Recommendation | Effort | Impact | Based on |
|---|---------------|--------|--------|----------|
| **R6** | **Graph-informed ranking** — boost Points with high EP confidence. | ~50 lines in sdk.py + ep.py | Tortoise's unique differentiator | Original analysis |
| **R7** | **Related session traversal** — given a session, find others sharing entities. | ~30 lines in projection.py | Surfaces related context without explicit clustering | Graphiti communities, Honcho peer-centric model |
| **R8** | **Cross-encoder reranking** (optional, LLM-powered) — pass top-K results through a cheap model for final scoring. | ~60 lines, depends on LLM config | Significant accuracy boost for complex queries | Graphiti cross-encoder recipes |
| **R9** | **Periodic IDF rebuild** — trigger after 50 new sessions or weekly. | ~20 lines in ingest.py | Prevents TF-IDF staleness in BM25 component | Industry practice |

### Adopt Later (Strategic)

| # | Recommendation | Effort | Impact | Based on |
|---|---------------|--------|--------|----------|
| **R10** | **Session search index as a separate ontology** in the memory orchestrator. Add `sessionIndex` to `ONTOLOGY_CYPHER` with dedicated Cypher templates. | ~80 lines in memory_orchestrator.py | Unifies search across all ontologies | DomainRouter architecture |
| **R11** | **Fact invalidation via temporal edges** — when a new Point contradicts an old one, mark old as outdated (valid_until = now) instead of deleting. | Requires schema change in projection | Preserves provenance, enables "what did we think before?" queries | Graphiti bi-temporal model |
| **R12** | **Session summaries for long sessions** — auto-generate a 3-5 sentence summary per session for quick scanning in search results. | LLM call per session, could be async | Agents see "this session was about X, decided Y" without reading 100 messages | Honcho session summaries |

---

## Architecture: Proposed Search Pipeline

```
Agent Query: "what did we decide about pricing?"
        │
        ▼
┌──────────────────────────────────────┐
│  1. Query Understanding               │
│  - Extract entities: ["pricing"]      │
│  - Detect query type: decision_recall │
│  - Expand with synonyms if needed     │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  2. Parallel Retrieval                │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ │
│  │ Vector   │ │ BM25    │ │ Graph   │ │
│  │ (embed)  │ │ (sparse)│ │ (Cypher)│ │
│  └────┬────┘ └────┬────┘ └────┬────┘ │
│       │           │           │       │
│       ▼           ▼           ▼       │
│  Points+Chunks  Points     Sessions   │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  3. Rank Fusion (RRF)                 │
│  - Merge result lists                 │
│  - RRF: score = Σ 1/(k + rank_i)      │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  4. Graph-Informed Boost              │
│  - Look up EP confidence per Point    │
│  - Boost high-confidence results      │
│  - Tag low-confidence as ⚠️ tentative  │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  5. Result Assembly                   │
│  - Top-K results with provenance      │
│  - Related sessions sidebar           │
│  - Confidence tags                    │
│  - Snippets with context              │
└──────────────────────────────────────┘
```

---

## Source Confidence Summary

| Claim | Tier | Sources |
|-------|------|---------|
| Hybrid retrieval (BM25 + vector + graph) is the industry standard | **HIGH** | Graphiti docs, Honcho docs, Anthropic contextual retrieval, Pinecone hybrid search guide |
| Search over extracted representations, not raw text | **HIGH** | Graphiti (edges/nodes), Honcho (conclusions), Mem0 (memories) |
| Delta-only incremental extraction with position tracking | **HIGH** | Graphiti episodic ingestion, Honcho async reasoning queue |
| RRF is the standard rank fusion method | **HIGH** | Graphiti (default), industry literature |
| Contextual chunking reduces retrieval failures ~35-49% | **MEDIUM** | Anthropic engineering blog (single practitioner source but with published methodology) |
| Graph-informed ranking via EP confidence is unique to Tortoise | **HIGH** | Direct analysis of Tortoise EP system vs all surveyed systems |
| Communities/clusters for session deduplication | **MEDIUM** | Graphiti communities, Honcho peer-centric model |
| Cross-encoder reranking improves accuracy for complex queries | **MEDIUM** | Graphiti docs, Mem0 reranker docs |
| Periodic IDF rebuild at 20% corpus growth or weekly | **LOW** ⚠️ single-source | Industry practice, not documented in surveyed systems |

---

## Open Questions

1. **What embedding model for session chunks?** The current `all-MiniLM-L6-v2` is 384-dim and fast. Consider `mixedbread-ai/mxbai-embed-large-v1` (1024-dim, MTEB leaderboard top) for higher quality if latency budget allows.

2. **Should we index every message or only "substantial" messages?** 2-word messages ("ok", "thanks") add noise. Graphiti/Honcho process everything because they extract facts, not index raw text. For raw chunk indexing, filter by minimum token count (>10 tokens).

3. **Real-time vs batch indexing?** tortoise-capture appends every few seconds. Batch indexing every N messages or every M seconds is simpler and nearly as good as real-time. Recommended: batch every 10 messages or 60 seconds, whichever comes first.

4. **How to handle cross-session context?** When an agent continues a conversation across sessions, should we surface the prior session's context automatically? Honcho's `session.context()` does this. We could auto-inject the last session's summary into new sessions about the same topic.

---

> **Research conducted 2026-07-18.** Sources: Graphiti (Zep) docs, Honcho docs, Mem0 docs, Anthropic Contextual Retrieval engineering blog, Pinecone hybrid search guide. Perplexity API unavailable — research limited to web_fetch of documentation sources.
