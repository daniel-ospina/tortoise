# GraphRAG Reference Systems (Microsoft GraphRAG + LightRAG)

> The two reference implementations of "retrieve a subgraph, serialize it into one context window, ask the LLM." Microsoft GraphRAG = entity-anchored local search over a corpus-scale graph index. LightRAG = the lightweight academic challenger that replaces community reports with dual-level keyword recall.

---

## 0. How to Read This Profile

Two systems, one profile. Per the skill's multi-product rule, each dimension uses sub-sections where the two differ, and a shared "Contrast" line where the difference is the point. Both are **index-over-corpus** systems: they build a graph once from a document set, then answer questions against it. Neither is a write-time memory store, and neither carries a reasoning state.

| | Microsoft GraphRAG | LightRAG |
|---|---|---|
| Type | Corporate research project + OSS library | Academic lab OSS framework |
| GitHub | [microsoft/graphrag](https://github.com/microsoft/graphrag) | [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG) |
| Stars | 35,947 | 39,577 |
| License | MIT | MIT |
| Query surface | local / global / DRIFT / basic | local / global / hybrid / naive / mix |

*Last checked: 2026-09-11*

---

## 1. Overview

### Microsoft GraphRAG

| Field | Value |
|---|---|
| Built by | Microsoft Research (MSR) — Redmond; maintained by a small MS team + community |
| Originated | Repo created 2024-03-27; paper arXiv 2404.16130 "From Local to Global: A Graph RAG Approach to Query-Focused Summarization" (Feb 2024) |
| HQ | Redmond, WA (Microsoft) |
| Funding raised | None (internal corporate R&D — not a venture-backed company) |
| Team size | ~1 primary maintainer visible in recent commits (Derek Worthen) + Copilot-assisted dependency sweeps |
| Markets | Global; developers, enterprises on Azure, research |
| Latest release | v3.1.2 (2026-08-21) |
| Repo status | Active but in **maintenance posture** — last commit 2026-08-24; recent history is dependency sweeps, docs, notebooks |

**Maintenance evidence (not editorializing):** the 10 most recent commits are dependency-update sweeps, a release cut, doc/spelling sweeps, and a "Cleanup" — no architectural change. Last commit 18 days before this profile's retrieval date.

### LightRAG

| Field | Value |
|---|---|
| Built by | HKUDS (HKU Data Intelligence Lab) + Beijing University of Posts and Telecommunications — Zirui Guo, Lianghao Xia, Yanhua Yu, Tu Ao, Chao Huang (corresponding: Chao Huang, Yanhua Yu) |
| Originated | arXiv 2410.05779 (2024-10-08; v3 2025-04-28); repo created 2024-10-02 |
| HQ | Hong Kong (University of Hong Kong) |
| Funding raised | None (academic lab project) |
| Team size | Large contributor base — ~340 contributors incl. anonymous, active daily |
| Markets | Global; developers, researchers, self-hosted/enterprise deployments |
| Venue | EMNLP 2025 ([repo description](https://github.com/HKUDS/LightRAG)) |
| Latest release | v1.5.7 (2026-09-02) |
| Repo status | **Highly active** — last commit 2026-09-11 (same day as this profile); 2,406 commits since 2026-06-01 |

*Last checked: 2026-09-11*

---

## 2. Product Type

### Microsoft GraphRAG

**Research system + OSS library.** Microsoft's own framing: a "modular graph-based Retrieval-Augmented Generation (RAG) system" ([repo description](https://github.com/microsoft/graphrag) — retrieved 2026-09-11) and a "research project." Ships as a Python package (`pip install graphrag`) + CLI (`graphrag index`, `graphrag query`).

**Commercial surface:** no separate paid GraphRAG product. It is distributed through Azure:
- **Microsoft Discovery** — GraphRAG and LazyGraphRAG "are now available through Microsoft Discovery" ([MSR project page](https://www.microsoft.com/en-us/research/project/graphrag/) — retrieved 2026-09-11); editor's note on the LazyGraphRAG blog also names **Azure Local** public preview (June 6, 2025 note).
- **Azure AI Search** — GraphRAG 1.0's supported vector stores include LanceDB and Azure AI Search ([MSR blog, "Moving to GraphRAG 1.0"](https://www.microsoft.com/en-us/research/blog/moving-to-graphrag-1-0-streamlining-ergonomics-for-developers-and-users/) — retrieved 2026-09-11). GraphRAG local search depends on a vector store (e.g. Azure AI Search) to find seed entities ([discussion #905](https://github.com/microsoft/graphrag/discussions/905)).
- **Azure-Samples/graphrag-accelerator** — "one-click deploy" sample, MIT, 2,408 stars — ⚠️ **archived**, last pushed 2025-05-27.

### LightRAG

**OSS framework only — no commercial product.** A Python library (`pip install lightrag-hku`) plus an in-repo **REST API server + WebUI** (`lightrag-server`, Docker images published to GHCR). Dual-layer architecture in their words: "manages both knowledge graphs (KGs) and vector embeddings, effectively bridging the gap between traditional vector-based RAG and graph-based RAG approaches" ([README](https://github.com/HKUDS/LightRAG) — retrieved 2026-09-11). No paid tier, no hosted offering from the authors; lab sibling projects (RAG-Anything, VideoRAG, MiniRAG) share the HKUDS umbrella.

**Contrast:** GraphRAG has a corporate distribution channel (Azure) but an archived accelerator and a coasting repo; LightRAG has no commercial channel but a live, fast-moving codebase.

*Last checked: 2026-09-11*

---

## 3. Positioning & Messaging

### Microsoft GraphRAG

**Tagline / self-description (verbatim):** "A modular graph-based Retrieval-Augmented Generation (RAG) system."

**Problem framing (their words, paper abstract):** "RAG fails on global questions directed at an entire text corpus, such as 'What are the main themes in the dataset?', since this is inherently a query-focused summarization (QFS) task, rather than an explicit retrieval task." ([arXiv 2404.16130](https://arxiv.org/abs/2404.16130) — retrieved 2026-09-11)

**Brand voice:** research-forward and honest about trade-offs. The docs consistently state *which* query class each mode is for ("It is well-suited for answering questions that require an understanding of specific entities…"), and the LazyGraphRAG blog openly concedes that "up-front indexing costs… may be prohibitive for some users and use cases."

### LightRAG

**Positioning line (verbatim, README):** "LightRAG is a lightweight knowledge-graph RAG framework and an efficient alternative to Microsoft GraphRAG."

**Value proposition (verbatim, README):** "Extreme Retrieval Efficiency & Low Cost: LightRAG does not rely on inefficient community reports or multi-hop reasoning for complex queries. This drastically reduces the number of LLM calls required during both the indexing and querying phases, significantly lowering response latency and LLM computational costs."

**Brand voice:** benchmark-and-feature-list. Badges, emoji release notes, "Outstanding" claims. Explicitly positions *against* GraphRAG by name — a rare direct attack in an academic OSS README.

[Sources](https://github.com/HKUDS/LightRAG), [arXiv 2410.05779](https://arxiv.org/abs/2410.05779) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 4. Target Audience

### Microsoft GraphRAG

**Primary:** developers and data/AI teams with a private document corpus and corpus-level ("global sensemaking") questions — news archives, podcast transcripts, research corpora, enterprise knowledge bases. Secondary: Azure customers who want the capability without running the pipeline (via Microsoft Discovery / Azure AI Search).

| Segment | Details |
|---|---|
| Personas | Applied AI engineers, RAG/retrieval engineers, enterprise architects, MSR-adjacent researchers |
| Use cases | Global sensemaking over a corpus; entity-centric Q&A where the question names an entity ("What are the healing properties of chamomile?" — the docs' own example) |
| Named users | ⚠️ No named enterprise customers published — Microsoft does not publish a GraphRAG customer list |

### LightRAG

**Primary:** developers building domain QA over private corpora who care about indexing/query cost and self-hosting — legal, financial, agriculture, academic. Also a research baseline: it appears as a compared system in third-party benchmarks (GraphRAG-Bench).

| Segment | Details |
|---|---|
| Personas | Python developers, RAG practitioners, academic researchers, self-hosted/air-gapped deployments |
| Use cases | Domain Q&A on large corpora (their UltraDomain evals: Agriculture, CS, Legal, Mix); multimodal document QA; enterprise internal knowledge with PostgreSQL/Neo4j backends |
| Explicit requirements | They rate LLMs *by role* (EXTRACT / QUERY / KEYWORD / VLM) and state the query LLM "needs… the capability of generating high-quality responses in long, noisy contexts" |

*Last checked: 2026-09-11*

---

## 5. Business Model & Pricing

**Both: MIT-licensed, no license fee. The real price is LLM tokens.** Neither publishes a price list.

### Microsoft GraphRAG

**Revenue model:** indirect — Microsoft product surface (Azure/Microsoft Discovery). The library itself is free.

**Cost signals (Microsoft's own numbers):**
- **LazyGraphRAG blog** (MSR, 2024-11-25): "LazyGraphRAG data indexing costs are identical to vector RAG and **0.1% of the costs of full GraphRAG**"; a LazyGraphRAG config "shows comparable answer quality to GraphRAG Global Search for global queries, but **more than 700 times lower query cost**"; and "For **4% of the query cost** of GraphRAG global search, LazyGraphRAG significantly outperforms all competing methods." ([source](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/) — retrieved 2026-09-11)
  - Implication stated plainly by Microsoft: **full GraphRAG indexing ≈ 1,000× vector-RAG indexing cost.**
- **Microsoft's own cost explainer:** "GraphRAG costs explained — what you need to know" ([Azure AI Foundry blog](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/graphrag-costs-explained-what-you-need-to-know/4207978) — retrieved 2026-09-11) frames cost benchmarking as the reason the accelerator exists.
- ⚠️ **LazyGraphRAG (the cheap index) is not the OSS `microsoft/graphrag` pipeline** — it is delivered via Microsoft Discovery / Azure Local. The free OSS repo is the expensive path.

### LightRAG

**Revenue model:** none. Free MIT library; author's institution funds development.

**Cost signals (their measurement, Legal dataset):**

| Phase | GraphRAG | LightRAG |
|---|---|---|
| Retrieval tokens | 610,000 (610 level-2 communities × ~1,000 tokens each) | <100 |
| Retrieval API calls | Hundreds ("traverse each community individually") | 1 |
| Incremental update | Must "dismantle its existing community structure" to re-ingest new data | Incremental insert; extraction cost only |

[arXiv 2410.05779 §4.5](https://arxiv.org/html/2410.05779v3) — retrieved 2026-09-11. Note this is an **adversarial measurement by the competitor**, published in their own paper — directionally useful, not a neutral audit.

*Last checked: 2026-09-11*

---

## 6. Product & Features

### 6a. MICROSOFT GRAPHRAG — Subgraph Selection & Serialization

**Index build.** Text units (chunks) → LLM entity/relationship extraction (optional claim/covariate extraction, `enabled = False` by default) → graph → Leiden hierarchical communities → LLM-generated community reports per community. Then optional graph pruning.

**Query-time selection (local search), in order — verified against source, not docs prose:**

1. **Seed by embedding.** `map_query_to_entities()` embeds the query and does a similarity search over entity-description embeddings with **`k = top_k_mapped_entities (default 10) × oversample_scaler (2)` = 20 candidates**, then filters excludes and appends user-specified includes. The code comment states the reason: *"oversample to account for excluded entities."* If the query is empty, it falls back to the top entities by rank.
2. **Fan out from the seed set to five candidate pools** (the docs' own dataflow): candidate **text units** (entity→text_unit mapping), candidate **community reports** (entity→community mapping), candidate **entities** (via entity-entity relationships), candidate **relationships** (entity-entity), candidate **covariates/claims** (entity-covariate mapping). This is a **1-hop fan-out from the seeds, not BFS/PPR.**
3. **Partition a single fixed budget.** `max_context_tokens` default **12,000** (the config default that governs the pipeline — the `build_context()` function signature itself falls back to 8,000 when called directly). Split by proportion:
   - `text_unit_prop = 0.5` → raw source text (~50%)
   - `community_prop = 0.15` → community reports (~15%)
   - residual `local_prop = 1 − 0.5 − 0.15 = 0.35` → entities + relationships + covariates
   - `local_prop` is computed, never configured: `local_prop = 1 - community_prop - text_unit_prop`
   - Conversation history is charged **against the same budget first**: `max_context_tokens -= tokens(conversation_history)` (default `conversation_history_max_turns = 5`, user turns only).
   - ⚠️ The widely repeated "~50% text / ~10% community reports" figure was the v1 default. Current [`defaults.py`](https://github.com/microsoft/graphrag/blob/main/packages/graphrag/graphrag/config/defaults.py) has community at **0.15**. (DRIFT search is the one still at 0.9/0.1.)
4. **Relationship admission ordering — in-network first.** `_filter_relationships()` states it in a comment: *"First priority: in-network relationships (i.e. relationships between selected entities)"*, then *"Second priority - out-of-network relationships"*, and within out-of-network it prioritizes *"mutual relationships (i.e. relationships with out-network entities that are shared with multiple selected entities)"*, then sorts by `rank`. All lists sort descending by rank (the entity/relationship "rank" = number of relationships, i.e. **degree**).
5. **Text units ranked by seed order, then relationship count.** `_build_text_unit_context()` sorts `(entity_order, -num_relationships)` — i.e. text belonging to earlier seeds wins, ties broken by how many of that entity's relationships appear in the chunk.
6. **Community reports ranked by match count, then rank:** number of selected entities in the community, descending.
7. **Truncation is a hard stop, not a summary.** Records are appended until the per-pool token cap is hit; on overflow the code logs *"Reached token limit - reverting to previous context state"* and breaks.

**Serialization — this is the part worth stealing.** The context is **pipe-delimited ("|") CSV-like tables under section headers**, not triples and not prose:

```
-----Entities-----
id|entity|description|number of relationships
...
-----Relationships-----
id|source|target|description
...
-----Reports-----
-----Sources-----
id|text
...
```

Entities carry optional attribute columns (auto-discovered from `entity.attributes`); relationships can include a `weight` column (`include_relationship_weight`, default off). Text units are emitted verbatim as raw chunks. The system prompt then instructs: *"You are a helpful assistant responding to questions about data in the tables provided"* and requires **inline record-id citations**:

> "This is an example sentence supported by multiple data references [Data: <dataset name> (record ids); <dataset name> (record ids)]." … "Do not list more than 5 record ids in a single reference. Instead, list the top 5 most relevant record ids and add '+more'…"

[Source: `mixed_context.py`, `local_context.py`, `entity_extraction.py`, `relationships.py`, `defaults.py`, `prompts/query/local_search_system_prompt.py` on `main`](https://github.com/microsoft/graphrag/tree/main/packages/graphrag/graphrag/query) — retrieved 2026-09-11.

**Serialization verdict:** structured **records in labeled tables** + a citation protocol keyed to record ids. Not triples, not NL summaries. The LLM reads a "data table" and must cite back into it.

### 6b. LIGHTRAG — Subgraph Selection & Serialization

**Index build.** Chunk → LLM extracts entities + relationships (`Recog`); then an LLM **profiling** function (`Prof`) generates a text key-value pair per node and edge — "Each index key is a word or short phrase that enables efficient retrieval, while the corresponding value is a text paragraph summarizing relevant snippets." **Entities use their name as the sole index key; relations get multiple LLM-generated keys including global themes from connected entities.** Then `Dedupe` merges identical entities/relations across chunks.

**Query-time selection:**
1. **One keyword-extraction LLM call per query.** The `keywords_extraction` prompt returns both `high_level_keywords` (overarching concepts/themes) and `low_level_keywords` (specific entities/attributes). Code path: `hl_keywords, ll_keywords = await get_keywords_from_query(...)`.
2. **Two vector recall surfaces:**
   - **Low-level** → low-level keywords matched against **entity** vectors/keyed profiles → matched entities + their attributes + **1-hop neighbors** (`N_v` neighbors of retrieved nodes, `N_e` neighbors of retrieved edges — the paper's `{v_i | v_i ∈ V ∧ (v_i ∈ N_v ∨ v_i ∈ N_e)}`).
   - **High-level** → high-level keywords matched against **relation** vectors → relation chains covering broad themes.
   - **hybrid** = local + global merged (relevance-based fusion); **mix** = local + global + **naive** (raw chunk vectors). `mix` is the default mode.
3. **Hybrid retrieval also blends vector + graph**: "the integration of graph structures with vector representations facilitates efficient retrieval of related entities and their relations."
4. **Budget:** `DEFAULT_MAX_TOTAL_TOKENS = 30000`, partitioned as entity tokens (6,000), relation tokens (8,000), remainder for chunks, minus a 200-token buffer for the reference list. The budget is computed against **the actual rendered prompt template** (a deliberate code comment explains this avoids mis-sizing when callers pass custom templates). Retrieval breadth: `DEFAULT_TOP_K = 40`, `DEFAULT_CHUNK_TOP_K = 20`. A **reranker** is supported (config-gated; enabling it "typically introduces a 1–2 second delay").

**Serialization — JSON Lines in labeled fenced blocks.** The `kg_query_context` template:

```
Knowledge Graph Data (Entity):
```json
{"entity_name": ..., ...}      ← one JSON object per line
```
Knowledge Graph Data (Relationship):
```json
{"src_id":..., "tgt_id":..., ...}   ← one JSON object per line
```
Document Chunks (Each entry has a reference_id …):
```json
{"reference_id":..., "content":..., "content_headings":...}
```
Reference Document List:
[n] <file_path>
```

Records are emitted with `json.dumps(entity)` per line. Entities carry `entity_name`, `source_id` (contributing chunk ids), `created_at`; relations carry endpoints, keywords, weight/rank, and `source_id`. The answer prompt requires a `### References` section citing `[n] Document Title` (max 5).

[Source: `lightrag/prompt.py` (`kg_query_context`, `keywords_extraction`, `rag_response`), `lightrag/operate.py` (`_build_query_context`), `lightrag/constants.py`](https://github.com/HKUDS/LightRAG) — retrieved 2026-09-11.

**Serialization verdict:** structured **JSON records in labeled blocks** + a separate reference list. Cheaper to produce than tables, more token-expensive per record, and trivially machine-parseable.

### 6c. Feature comparison

| Capability | Microsoft GraphRAG | LightRAG |
|---|---|---|
| Corpus-wide ("global") answers | ✅ community reports + map-reduce | ✅ high-level (relation) recall |
| Local/entity-anchored answers | ✅ local search (5 candidate pools) | ✅ low-level (entity) recall + 1-hop |
| Query expansion into follow-ups | ✅ DRIFT (community-primed + follow-up questions + rerank) | ❌ no equivalent |
| Incremental indexing | ✅ now supported in 3.x (`index/update/incremental_index.py` — computes `new_inputs`/`deleted_inputs` deltas, plus a family of `update_*` workflows) — after a long-standing gap ([#741](https://github.com/microsoft/graphrag/issues/741), 35 comments) | ✅ incremental insert + **selective deletion** from LLM cache |
| Pluggable graph store | via vector store abstraction (LanceDB, Azure AI Search) | Neo4j, Memgraph, PostgreSQL, MongoDB, OpenSearch, Milvus, Qdrant, NetworkX |
| Reranking | ❌ | ✅ supported (config-gated; ~1–2s latency) |
| Multimodal (images/tables/formulas) | ❌ | ✅ v1.5+ (VLM role, Docling/MinerU parsers) |
| Claims with a status field | ✅ opt-in (`TRUE`/`FALSE`/`SUSPECTED`), **disabled by default** | ❌ |
| Graph pruning | ✅ `prune_graph`: `min_node_freq=2`, `min_node_degree=1`, `min_edge_weight_pct=40.0`, `remove_ego_nodes=True` | ❌ (dedupe only) |
| MCP server | ❌ not in repo | ❌ not in repo |
| LazyGraphRAG (cheap index) | ❌ **not in the OSS repo** — no `lazy` module on `main`; delivered only via Microsoft Discovery / Azure Local | n/a |

**Notable gaps (GraphRAG):** expensive index (Microsoft's own framing); a single fixed seed mechanism (embedding over entity descriptions) with no topic/theme key surface; no reranker; the cheap path (LazyGraphRAG) is not in the OSS repo.

**Notable gaps (LightRAG):** no entity resolution/alias merging ([open issue #1323](https://github.com/HKUDS/LightRAG/issues/1323), 34 comments); no contradiction handling; retrieval quality degrades with weak extraction LLMs (default storages are in-memory and "not suitable for production" in their own words).

*Last checked: 2026-09-11*

---

## 7. Go-to-Market & Acquisition

### Microsoft GraphRAG

| Channel | Activity |
|---|---|
| Microsoft Research blog | Launch post + follow-ups: dynamic community selection (2024-11-15), LazyGraphRAG (2024-11-25), "Moving to GraphRAG 1.0" |
| Academic paper | arXiv 2404.16130 — the canonical citation for the category |
| Azure distribution | Microsoft Discovery (GA path), Azure Local (public preview), Azure AI Search vector store support |
| OSS funnel | `microsoft/graphrag` (35.9k stars) + archived accelerator (2.4k stars) |
| Package registry | PyPI `graphrag` — 11,442 downloads/week |

**Sales motion:** open-source → Azure attach. No direct sales of the library.

### LightRAG

| Channel | Activity |
|---|---|
| Academic publication | EMNLP 2025 |
| OSS funnel | 39.6k stars, 5.6k forks, ~340 contributors, Trendshift badge |
| Community | Discord (2,161 members, 158 online) + WeChat group |
| Packaging | PyPI `lightrag-hku` (29,572/week), Docker/GHCR images, one-command setup wizard |
| Direct competitive positioning | README names Microsoft GraphRAG as the thing it is an "efficient alternative" to |
| Lab halo | Sibling HKUDS projects (RAG-Anything, VideoRAG, MiniRAG) cross-promoted in release notes |

**Sales motion:** pure OSS/PLG-by-citation. No sales team; the paper is the marketing.

*Last checked: 2026-09-11*

---

## 8. Traction & Scale

> **Dev-tool metrics** (stars, forks, PyPI, citations, contributors), not B2C metrics — as appropriate for OSS infrastructure. All 2026-09-11 except where dated.

| Signal | Microsoft GraphRAG | LightRAG |
|---|---|---|
| GitHub stars | **35,947** | **39,577** |
| Forks | 3,781 | 5,570 |
| Open issues | 54 | 232 |
| Contributors (incl. anonymous) | ~51 | ~340 |
| Commits since 2026-06-01 | 23 | 2,406 |
| Last commit | 2026-08-24 | 2026-09-11 |
| Latest release | v3.1.2 (2026-08-21) | v1.5.7 (2026-09-02) |
| PyPI package | `graphrag` — 2,483/day, 11,442/week, 52,051/month | `lightrag-hku` — 5,046/day, 29,572/week, 231,987/month |
| Citations (paper) | ⚠️ **not verified** — Semantic Scholar returned HTTP 429 on 4 attempts | **463 citations, 67 influential** (Semantic Scholar, EMNLP) |
| Community platform | GitHub Discussions | Discord 2,161 members (158 online); WeChat |
| Conference venue | arXiv preprint (not peer-reviewed at a venue) | EMNLP 2025 |
| Named customers / revenue | ⚠️ none published (no standalone product) | ⚠️ none (no commercial entity) |

**Read of the numbers:** LightRAG leads on every OSS-consumption metric — 2.6× the weekly PyPI downloads, 4.5× the monthly downloads, 2.0× the daily downloads, 6.7× the contributors, ~105× the recent commit volume. GraphRAG leads on nothing except having a corporate parent and a peer-reviewed-adjacent distribution channel. The one metric GraphRAG theoretically wins — paper citations — is unverified; the paper is the origin of the term "GraphRAG" and is certainly heavily cited, so treat the gap as unknown rather than in LightRAG's favor.

*Last checked: 2026-09-11*

---

## 9. Online Presence & Content

| Channel | Microsoft GraphRAG | LightRAG |
|---|---|---|
| Docs site | [microsoft.github.io/graphrag](https://microsoft.github.io/graphrag/) — structured, with a query-mode overview and per-mode pages | In-repo docs (`docs/*.md`, dozens of guides) + API server docs |
| Content strategy | MSR research blog (mechanism deep-dives + honest cost posts), Jupyter notebooks per search mode | Release-note-driven README with dated feature log; 4 README translations (zh/ja/id) |
| Canonical content | The paper + the local/global search doc pages + the "costs explained" post | The paper (arXiv HTML) + README "Features & Advantages" list |
| Comparison content | LazyGraphRAG post benchmarks against vector RAG, RAPTOR, and GraphRAG's own modes | README explicitly benchmarks against Microsoft GraphRAG |
| Third-party citation surface | `graphrag.com` (independent reference site), DeepWiki auto-docs | Neo4j developer blog "Under the covers with LightRAG", DeepWiki auto-docs |

**SEO relevance to Tortoise:** none — neither targets the local-commerce/deals keyword space. Their content competes in "graph RAG", "knowledge graph RAG", "GraphRAG vs vector RAG" — the technical-comparison keywords a memory-engine buyer or evaluator would search before reading Tortoise's docs.

⚠️ No domain-authority or traffic estimates retrieved (paid tools not available for this run).

*Last checked: 2026-09-11*

---

## 10. Community & Ecosystem

| Channel | Microsoft GraphRAG | LightRAG |
|---|---|---|
| GitHub stars | 35,947 | 39,577 |
| Contributors | ~51 | ~340 |
| Open issues | 54 | 232 |
| Chat | GitHub Discussions | Discord 2,161 members / 158 online; WeChat group |
| Integrations | Azure AI Search; LanceDB; any OpenAI-compatible LLM | Neo4j, Memgraph, PostgreSQL, MongoDB, OpenSearch, Milvus, Qdrant, NetworkX; MinerU/Docling parsers; VLM support |
| Downstream reimplementations | Many (nano-graphrag, Fast-GraphRAG, community forks; LazyGraphRAG itself is a re-architecture) | Many (appears as a baseline in GraphRAG-Bench and other benchmark suites) |

**Community mechanics:** GraphRAG's community is *consumptive* — research and enterprise teams adopt and fork, but upstream commits come from Microsoft. LightRAG's is *contributive* — ~340 contributors and daily merges from outside the lab, plus dependabot churn. The high issue count (232) with a high contributor count is the signature of a system people build on rather than read about.

*Last checked: 2026-09-11*

---

## 11. Customer Sentiment

**Sources checked:** GitHub issues (top by comment volume) on both repos, published third-party benchmarks (GraphRAG-Bench, DEG-RAG), Microsoft's own blog concessions, Reddit/HN commentary surfaced via search.

### Microsoft GraphRAG

**What people value:**
- The global-sensemaking capability — it is the reference answer to "what are the themes in this corpus?" (the paper's own evaluation target).
- Microsoft's transparency: they published the cost problem themselves ("up-front indexing costs… may be prohibitive") and shipped LazyGraphRAG/DRIFT as responses.

**What people complain about:**
- **Cost.** The most-liked open question in repo history is literally ["When will LazyGraphRAG arrive?"](https://github.com/microsoft/graphrag/issues/1512) (44 comments) — i.e. "when does the cheap version reach the free repo?"
- **Historically: no incremental indexing.** [#741 "Incremental indexing (adding new content)"](https://github.com/microsoft/graphrag/issues/741) drew 35 comments and stood as the repo's most-requested gap for a long time. It is now addressed in 3.x (`index/update/incremental_index.py`) — the pain it caused is still the dominant memory in the community.
- **Operational friction.** [#339 "[Ollama][Other] GraphRAG OSS LLM community support"](https://github.com/microsoft/graphrag/issues/339), 68 comments — the highest-comment issue in the repo.
- **Measured losses to plain RAG.** GraphRAG-Bench (arXiv 2506.05690) cites Han et al. 2025: GraphRAG is **13.4% lower accuracy on Natural Questions** than vanilla RAG, with a **16.6% accuracy drop on time-sensitive questions**; graph retrieval adds only **+4.5% on HotpotQA multi-hop** while costing **2.3× latency** (Zhou et al. 2025).
- **Extraction noise.** DEG-RAG (arXiv 2510.14271 / OpenReview) reports that **removing ~40% of entities and relations from LLM-generated graphs improves downstream QA** — the corpus-equivalent of "your graph is partly garbage." GraphRAG's own `prune_graph` defaults (`min_node_freq=2`, `min_edge_weight_pct=40`) are an implicit admission of the same.

**Overall sentiment:** Mixed-to-positive on capability, negative on cost and freshness. Respected as the origin of the category; not used as a production RAG default.

### LightRAG

**What people value:**
- Genuine cost/latency advantage with no community-report build step (measured in its own §4.5, and corroborated by GraphRAG-Bench's **medical** retrieval table, where LightRAG scores **63.32 recall vs MS-GraphRAG local's 38.06**).
- Incremental insert **and selective delete** — the thing GraphRAG users ask for most.
- Operational breadth: real production backends (PostgreSQL recommended), reranker, multimodal parsing, REST API + WebUI.

**What people complain about:**
- **Entity resolution.** The top open feature request is ["Automatic merging of the same entity under different names"](https://github.com/HKUDS/LightRAG/issues/1323) (34 comments) — alias/duplicate entities survive into retrieval.
- **Extraction quality is the ceiling.** Highest-comment issue ever: ["Entity Extraction Failure: No Entities or Relationships Extracted with ollama models"](https://github.com/HKUDS/LightRAG/issues/30) (42 comments). Weak LLM → empty graph → no retrieval.
- **Graph-structure-induced failure.** Community issue #3234 (38 comments) documents failures attributed to dangling pronouns and graph structure — entity references that need cross-chunk resolution.
- **Context noise by design.** Their own README says the query LLM must handle "long, noisy contexts" — an acknowledgment, not a defense.
- **Evaluation skepticism.** The paper's headline results are **LLM-judged win rates on a self-constructed 125-question-per-domain setup** (`Comprehensiveness` / `Diversity` / `Empowerment`), not accuracy on a held-out QA benchmark. Versus GraphRAG the margin is thin (**49.6%–54.8% overall** on the Mix and CS/Legal domains — i.e. near-parity, with Diversity carrying the difference).

**Overall sentiment:** Positive on cost and engineering velocity; mixed on knowledge-graph quality. The market treats it as the practical default when you cannot pay GraphRAG's indexing bill — while independently measured results (GraphRAG-Bench) show it is simply *the least-damaged member of a category that loses to vanilla RAG on simple fact retrieval*.

*Last checked: 2026-09-11*

---

## 12. Architectural Contrast vs Tortoise (the point of this profile)

Both systems are **index-over-corpus**: build a graph from a static document set, then answer questions. Tortoise is a **write-time reasoning state**: typed epistemic operators (IMPL, NAND), belief propagation into confidence weights, temporal validity/supersession, and provenance to the source conversation turn. The relevant comparison is not "product vs product" but "their retrieval+serialization pipeline vs ours."

### 12a. Confirm/correct: "no belief, no logic operators"

**Mostly confirmed, with one correction for GraphRAG.**

| Property | Microsoft GraphRAG | LightRAG |
|---|---|---|
| Graph? | ✅ entity graph + Leiden community hierarchy | ✅ entity/relation graph |
| Confidence / credence? | ❌ **None.** Entity `rank` and relationship `rank`/`weight` are **degree and extraction-strength**, not belief. Nothing propagates. | ❌ None. Relation "weight" is a strength/co-occurrence score. Nothing propagates. |
| Logical operators (IMPL/NAND)? | ❌ None. Edges are free-text `description` strings between two entities. | ❌ None. Edges are keyword-keyed free-text descriptions. |
| Contradiction handling? | ❌ None. Community reports can and do contain contradictory statements (a documented GraphRAG critique: chunks extracted in isolation yield "contradictory facts, orphaned nodes"). | ❌ None. Duplicate-entity merging is the only reconciliation; contradictions persist side by side. |
| ⚠️ **Correction** | GraphRAG *does* have an **optional claim layer**: `extract_claims` (default `enabled = False`) extracts claims with a **`Claim Status: TRUE / FALSE / SUSPECTED`** field, a **claim type**, an ISO-8601 **claim date range**, and source quotes. It is a *label*, not a state: nothing consumes the status — it is not aggregated, not propagated, not queried, and does not affect retrieval ranking. It is a shallow analytic annotation, not an epistemic system. | — |
| Temporal validity / supersession? | ❌ No `valid_to`. Claim *dates* exist as attributes in the opt-in claim layer; supersession is not modeled. | ❌ `created_at` only (ingest timestamp). No validity intervals, no supersession. |
| Provenance? | ✅ **Record-id level, and it is enforced in the prompt.** Every text unit has an id; the system prompt requires `[Data: Sources (15, 16), Reports (1), Entities (5, 7); Relationships (23); Claims (2, …)]`. Text units map to source documents/chunks — not to conversation turns. | ✅ **File/chunk level.** Every entity and relation carries `source_id` (contributing chunk ids); chunks carry `reference_id`; the answer prompt requires a `### References` list of `[n] Document Title`. Not turn-level. |

**Verdict:** The claim that these are "index-over-corpus systems with no notion of belief/credence and no logical operators" is **correct**, with the nuance that GraphRAG ships a disabled-by-default claim-status annotation that resembles an epistemic primitive but functions as an inert attribute. Neither has belief propagation, NAND/IMPL edges, or supersession. Both have *flat record-id provenance*, which is real but weaker than turn-level provenance.

### 12b. What is genuinely reusable for a memory system

| Pattern | Where it comes from | Why it transfers |
|---|---|---|
| **Budget-partitioned context assembly.** One fixed token total, split by proportion across source classes (text 50% / reports 15% / entities+relations+covariates 35%), each pool independently capped. | GraphRAG `mixed_context.py` | A reasoning subgraph has heterogeneous content (claims, operators, mitigations, provenance turns). Fixed total + per-class proportions makes serialization deterministic and prevents one class (e.g. long source turns) from crowding out the operators that carry the reasoning. |
| **Charge conversation history against the same budget, first.** | GraphRAG `build_context()` | Prevents the classic "history + retrieval > window" overflow. Compute it once, subtract it. |
| **In-network-first ordering.** Admit edges *between already-selected nodes* before edges that expand outward, then prefer expansion targets shared by multiple seeds. | GraphRAG `_filter_relationships()` (comment: "First priority: in-network relationships") | Directly applicable: serialize the *internal* structure of the selected reasoning region before pulling in frontier nodes. Keeps the subgraph causally coherent instead of telling discontiguous stories. |
| **Record-id citation protocol inside the answer.** Instruct the model to cite `[Data: <table> (ids)]`, cap at 5 ids + "+more". | GraphRAG local search system prompt | Maps onto Tortoise's provenance: force every generated claim to name the point ids it came from. Cheap, and it's an *enforced* contract, not a hope. |
| **Dual-level query keys (specific entity vs abstract theme) → two recall surfaces.** One LLM call extracts both; entity keys hit entity vectors, theme keys hit relation/topic vectors; hybrid unions them. | LightRAG §3.2 + `keywords_extraction` prompt | Useful framing for a memory query router: "which specific points?" vs "which reasoning themes?" — and it costs exactly one cheap LLM call. Note the abstract keys are generated **at index time** for relations ("relations may have multiple index keys derived from LLM enhancements that include global themes from connected entities") — that is the reusable trick, not the query-time extraction. |
| **Incremental insert + selective delete with an extraction cache.** | LightRAG (and now GraphRAG 3.x's `update_*` workflows, after [#741](https://github.com/microsoft/graphrag/issues/741) sat open for a long time) | Table stakes for memory: a memory system that requires full re-index to add one turn is not a memory system. LightRAG's "reuse the cached extraction when a doc is deleted" is the right shape. |
| **Compute the budget against the actually-rendered template.** LightRAG explicitly charges the real template (including any user-prompt prefix), with a code comment explaining that estimating against the default template silently mis-sized custom ones. | LightRAG `_build_query_context` | A correctness detail worth copying verbatim in spirit: serialize, then measure, then re-measure. |
| **Structured records beat prose for machine-checkable context; JSON beats CSV when you control the producer.** | Both | GraphRAG emits pipe-delimited tables (token-efficient, human-readable, weaker typing); LightRAG emits JSONL (self-describing, more tokens). For a reasoning graph with typed operators, JSONL with typed fields is the right default — the type is the payload. |

### 12c. What is corpus-QA-specific and NOT applicable to conversational agent memory

| Pattern | Why it does not transfer |
|---|---|
| **Community reports / Leiden hierarchy / global map-reduce.** | They exist to answer "what are the themes of this 1M-token corpus?" — a summarization-over-a-static-corpus problem. A memory system is asked about *a user's* history, not the corpus's themes. Generating reports is 99.9% of GraphRAG's index cost (per Microsoft's own LazyGraphRAG numbers) to serve a query class memory rarely gets. |
| **Global search as a first-class mode.** | Requires a bounded, frozen corpus to summarize. Conversation memory is unbounded and append-only; there is no "level-2 community" that stays valid. |
| **Seed-by-embedding over entity descriptions, with 2× oversampling.** | Reasonable for corpus QA. For memory, the seed is usually *structural* (the entity the user just mentioned, the active thread) — query embedding loses the referent. Keep the fan-out; replace the seed. |
| **Degree/rank as the primary ranking signal.** | In a corpus, degree ≈ importance. In a conversation-derived graph, high degree = a hub entity mentioned in passing. Ranking by degree actively surfaces noise, and hub nodes inflate the context — the documented over-expansion failure mode. Tortoise should rank by epistemic weight, not connectivity. |
| **Entity-resolution-by-dedupe as the only reconciliation.** | LightRAG's open top feature request is exactly this. A memory system cannot defer alias merging: the same person/company mentioned twice *is* the same subject, and merging them post-hoc destroys the supersession chain. |
| **LLM-judged win rates as the evaluation.** | Both papers' headline results are LLM-judged `Comprehensiveness`/`Diversity`/`Empowerment` win rates — GraphRAG on 125 questions × 5 repeats over podcasts/news; LightRAG on 125 questions per domain over textbooks. Third-party accuracy benchmarks (GraphRAG-Bench) show both **losing to basic RAG on simple fact retrieval**: on the novel dataset, MS-GraphRAG local scored 39.89 Fact-Retrieval ACC vs basic RAG's 46.74 (Qwen2.5-14B, same table); LightRAG scored 64.43 in GraphRAG-Bench's open-source-model generation table. If Tortoise adopts their eval methodology it will inherit their blind spot: a metric that rewards coverage and variety is blind to whether the claim is *true*, *current*, or *uncontradicted*. |
| **The assumption that "more context = more answer."** | Both systems' failure modes converge here: GraphRAG-Bench notes GraphRAG's extra processing "can introduce redundant or noisy information, which may degrade answer quality"; DEG-RAG shows deleting 40% of the graph helps. For a belief-carrying graph the analogous move is **pruning by credence and dropping superseded points before serialization** — which neither system can do, because neither has a credence to prune by. |

### 12d. The gap this leaves for Tortoise

GraphRAG and LightRAG have both independently converged on the *mechanics* Tortoise needs (seed → fan out → budget → serialize records → force citations) while both stopping short of the *semantics* Tortoise is built on. The published record is unusually favorable ground:

1. **They proved the pipeline** — 75,524 combined GitHub stars, 284,038 combined monthly PyPI downloads, 463 citations for LightRAG alone, EMNLP 2025 and a Microsoft research program behind them. "Retrieve a subgraph, serialize it into a window, cite the records" is not a hypothesis; it is the state of the art.
2. **They documented that the pipeline alone is not enough** — measurable losses to vanilla RAG on factual and time-sensitive questions (13.4% / 16.6% per Han et al., cited by GraphRAG-Bench), 2.3× latency for +4.5% multi-hop, ~40% of the extracted graph being removable noise (DEG-RAG), and neither system able to answer "which of these two facts supersedes the other?" or "how confident is this?" because neither stores those properties.
3. **The gap is exactly the differentiator** — typed operators (IMPL/NAND), propagated confidence, temporal validity/supersession, turn-level provenance. A memory answer that can be *filtered by credence and contradiction before it is serialized* is a strictly different artifact from a ranked list of records that fits in 12,000 tokens. That is the claim to make, and it is falsifiable against their published numbers rather than against taste.

*Last checked: 2026-09-11*

---

## Notes & Sources

**Primary sources (fetched 2026-09-11):**
- [microsoft.github.io/graphrag — Query Overview](https://microsoft.github.io/graphrag/query/overview/) — local/global/DRIFT/basic search definitions, verbatim
- [microsoft.github.io/graphrag — Local Search](https://microsoft.github.io/graphrag/query/local_search/) — "Entity-based Reasoning" framing + the local search dataflow diagram
- **GraphRAG source on `main`** (read directly, not via docs): [`query/structured_search/local_search/mixed_context.py`](https://github.com/microsoft/graphrag/blob/main/packages/graphrag/graphrag/query/structured_search/local_search/mixed_context.py) (defaults, proportions, ordering, truncation), [`query/context_builder/local_context.py`](https://github.com/microsoft/graphrag/blob/main/packages/graphrag/graphrag/query/context_builder/local_context.py) (table serialization), [`query/context_builder/entity_extraction.py`](https://github.com/microsoft/graphrag/blob/main/packages/graphrag/graphrag/query/context_builder/entity_extraction.py) (2× oversample), [`query/input/retrieval/relationships.py`](https://github.com/microsoft/graphrag/blob/main/packages/graphrag/graphrag/query/input/retrieval/relationships.py) (in-network-first), [`config/defaults.py`](https://github.com/microsoft/graphrag/blob/main/packages/graphrag/graphrag/config/defaults.py) (0.5/0.15/12,000; prune defaults; claim defaults), [`prompts/query/local_search_system_prompt.py`](https://github.com/microsoft/graphrag/blob/main/packages/graphrag/graphrag/prompts/query/local_search_system_prompt.py) (citation protocol), [`prompts/index/extract_claims.py`](https://github.com/microsoft/graphrag/blob/main/packages/graphrag/graphrag/prompts/index/extract_claims.py) (TRUE/FALSE/SUSPECTED)
- [arXiv 2404.16130 — From Local to Global](https://arxiv.org/abs/2404.16130) + [HTML v2](https://arxiv.org/html/2404.16130v2) (Evaluation §5.1; graph sizes 8,564/20,691 podcast, 15,754/19,520 news)
- [MSR blog — Improving global search via dynamic community selection](https://www.microsoft.com/en-us/research/blog/graphrag-improving-global-search-via-dynamic-community-selection/) (2024-11-15)
- [MSR blog — LazyGraphRAG: Setting a new standard for quality and cost](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/) (2024-11-25; Azure Discovery/Azure Local editor's note June 2025)
- [MSR Project GraphRAG](https://www.microsoft.com/en-us/research/project/graphrag/)
- [LightRAG README](https://github.com/HKUDS/LightRAG/blob/main/README.md) — positioning, features, query modes, storage backends, model roles
- LightRAG source: [`lightrag/prompt.py`](https://github.com/HKUDS/LightRAG/blob/main/lightrag/prompt.py) (`kg_query_context`, `keywords_extraction`, `rag_response`), [`lightrag/operate.py`](https://github.com/HKUDS/LightRAG/blob/main/lightrag/operate.py) (`_build_query_context`), [`lightrag/constants.py`](https://github.com/HKUDS/LightRAG/blob/main/lightrag/constants.py) (DEFAULT_TOP_K=40, DEFAULT_MAX_TOTAL_TOKENS=30000, etc.)
- [arXiv 2410.05779 LightRAG](https://arxiv.org/abs/2410.05779) / [HTML v3](https://arxiv.org/html/2410.05779v3) — §3.2 dual-level retrieval, §3.3 generation, §4.1 settings, §4.5 cost
- [arXiv 2506.05690 — When to use Graphs in RAG (GraphRAG-Bench)](https://arxiv.org/html/2506.05690v3) — the Han et al. 2025 / Zhou et al. 2025 figures (-13.4%, -16.6%, +4.5%, 2.3×); the MS-GraphRAG-local recall 38.06 vs RAG 86.24 medical result; LightRAG 63.32 novel-dataset recall
- [DEG-RAG — Less is More: Denoising Knowledge Graphs](https://arxiv.org/html/2510.14271v1) / [OpenReview](https://openreview.net/forum?id=y1EQ5EH5zF) — ~40% entity/relation removal improves QA

**Metrics (all retrieved 2026-09-11):** GitHub REST API — `microsoft/graphrag` 35,947★ / 3,781 forks / 54 open issues / ~51 contributors / v3.1.2 / last commit 2026-08-24; `HKUDS/LightRAG` 39,577★ / 5,570 forks / 232 open issues / ~340 contributors / v1.5.7 / last commit 2026-09-11; `Azure-Samples/graphrag-accelerator` 2,408★ / **archived** (last push 2025-05-27). PyPI via pypistats.org. Discord invite API: 2,161 members. Semantic Scholar: LightRAG 463 citations / 67 influential, EMNLP.

**Explicit gaps (nothing fabricated to fill them):**
- ⚠️ **GraphRAG paper citation count not verified.** Semantic Scholar returned HTTP 429 on four attempts across ~4 minutes. Do not cite a number for arXiv 2404.16130.
- ⚠️ **No named enterprise customers for either system.** Microsoft publishes no GraphRAG customer list; LightRAG has no commercial entity.
- ⚠️ **No revenue, funding, or valuation data** — neither is a funded company. Not applicable rather than missing.
- ⚠️ **LazyGraphRAG's cost claims (0.1% indexing, 700×/4% query) are Microsoft's own, in a marketing blog, without a reproducible artifact in the OSS repo.** Treated as directional.
- ⚠️ **LightRAG's §4.5 cost comparison is a competitor-authored measurement of its rival.** Directionally consistent with Microsoft's own cost admissions, but not a neutral audit.
- ⚠️ **LightRAG's evaluation is self-authored and LLM-judged** (win rates on 125 questions/domain over UltraDomain textbooks, `Comprehensiveness`/`Diversity`/`Empowerment`). Not accuracy on a held-out benchmark. Its near-parity result vs GraphRAG (49.6%–54.8% overall) should be read as "competitive", not "superior", except on Diversity.
- ⚠️ LightRAG Discord member count is a point-in-time API reading; GraphRAG has no comparable chat platform to count against.
- ⚠️ **No traffic/domain-authority figures** for either docs property (paid tools unavailable this run).
- ⚠️ Microsoft "Discovery" packaging is behind a product surface; the exact OSS↔commercial feature boundary (which of local/global/DRIFT/Lazy is available where) is not fully documented publicly.

*Last updated: 2026-09-11*
