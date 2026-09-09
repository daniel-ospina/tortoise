# Research Brief — Competitor Agentic Long-Term Memory Architectures: How the Reader Window Is Assembled, and Whether Anyone Solves "More Recall Hurts Accuracy"

**Date:** 2026-09-09 · **Mode:** external research (web, read-only) · **Language:** English
**Problem context (internal):** Tortoise = epistemic-graph memory (LLM extracts typed Points + entity/Object anchors + support/negate/mitigate operator edges; hybrid retrieval = sparse FTS + vector + structural; one reader LLM answers over a retrieved ~40-item evidence window). Benchmark: **LongMemEval 500-Q**. Measured issue: **improving retrieval recall does not improve end-to-end answer accuracy; the reader window caps the benefit and added evidence can degrade answers.**

**Extends (does not duplicate) in-repo prior art:**
- `docs/research/2026-09-08-graphrag-retrieval-evidence-assembly.md` — GraphRAG-family/KG read-side assembly, typed-slice token budgets, lost-in-the-middle, DEG-RAG dedup, GraphRAG-Bench.
- `docs/research/2026-09-08-connected-assembly-read-paths.md` — source-verified read paths of Mem0/Zep/Letta/LangMem/OpenAI/Bedrock/HippoRAG/GraphRAG/A-MEM/Generative Agents.
- `docs/research/2026-09-02-temporal-reasoning-competitors.md` — write-side temporal machinery + benchmark-number caveats.
This brief owns: **the exact RETRIEVAL→READER assembly each production system ships today (2026 docs), the selection/compression stage between retrieval and the model, and the "does anyone solve the recall↛accuracy problem" question.** Fresh web sources are cited with URLs; where this brief restates a prior doc's source-verified finding, it re-cites the primary URL.

## Source-trust legend (applied throughout)

| Tag | Meaning |
|---|---|
| **[vendor docs]** | Vendor documentation / engineering blog — mechanism-level claims; reliable for *how the system works*, not for comparative numbers |
| **[self-reported]** | Benchmark numbers published by the system's own authors/vendor — treat as upper bounds unless an independent eval corroborates |
| **[independent]** | Third-party evaluation **not** affiliated with the system's authors |
| **[vendor-run 3rd party]** | Evaluation run by a *different* vendor (often a competitor) — third-party but conflicted |
| **[paper]** | Peer-reviewed / arXiv publication |
| **[ABSENCE]** | Searched, no evidence found |

**⚠️ Benchmark-number reality (established in-repo 2026-09-02, re-verified this session):** vendor LongMemEval numbers differ from third-party/independent evals by **roughly 8–45 points depending on era and reader-model pairing** — largest for Mem0 (self 93.4/94.4 vs competitor-run 49.0 ≈ 44–45 pts), smallest for matched-era Zep (self 71–72 vs competitor-run 63.8 ≈ 8 pts); no two vendors run the same protocol, reader model, or split. All "~85%+" marketing numbers are upper-bound self-reports. LongMemEval-S (our harness) ≠ vendor LongMemEval full runs — not apples-to-apples.

---

## 0. What LongMemEval actually is (grounding)

LongMemEval (Wu et al., ICLR 2025, arXiv:2410.10813) = **500 hand-curated questions** embedded in scalable user–assistant chat histories, testing five core abilities: **information extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention**. Key structural facts: (1) the benchmark ships **oracle metadata** (ground-truth labels of which sessions contain the answer) so **retrieval can be measured separately from end-to-end QA**; (2) the paper's own framing decomposes a memory system into **indexing → retrieval → reading** stages — i.e., the field already treats "reading" (reader assembly) as a distinct design surface. The authors' own optimizations (session decomposition to "value granularity", fact-augmented key expansion, time-aware query expansion) report gains in **both recall and downstream QA** — a search-side paper. Sources: https://arxiv.org/abs/2410.10813 ; retrieval-only methodology discussed at https://dev.to/bozbuilds/perfect-retrieval-recall-on-the-hardest-ai-memory-benchmark-running-fully-local-5dhc

**Direct evidence for the Tortoise problem statement (recall ≫ end-to-end accuracy):**
- **Aingram (independent-ish, vendor-run)** ran retrieval-only on LongMemEval using the oracle split and the noisy LongMemEval-S split. Result: on the *noisy* S split, `recall_any@10 = 0.955` (relevant session in top 10 for 95.5% of queries) — yet published end-to-end systems (Zep 71.2%, Emergence 86%) sit far below that ceiling. Their conclusion: *"your end-to-end accuracy cannot exceed your retrieval recall… A system with recall_any@10 = 0.71 can at best achieve 71% end-to-end accuracy… the context ceiling for end-to-end accuracy is set by LLM reasoning, not by retrieval failure."* Oracle-run recall = 1.0 across all 500 queries but oracle ≠ 100% end-to-end accuracy — **even perfect evidence does not produce perfect answers, and most real systems lose 20–40 pts between recall and answer.** https://dev.to/bozbuilds/perfect-retrieval-recall-on-the-hardest-ai-memory-benchmark-running-fully-local-5dhc
- **RAG noise literature** (independent, peer-adjacent): "Long-Context LLMs Meet RAG" (arXiv:2410.05983) — more retrieved passages raise recall but **lower precision**, and irrelevant hard-negative distractors mislead the LLM and cut answer accuracy; U-NIAH (arXiv:2503.00353) — retrieval noise degrades performance and **more chunks = more noise = more hallucinations**; "RAG as Noisy In-Context Learning" (arXiv:2506.03100) — the benefit of extra retrieved items **shrinks and can flip to harm** as count grows; Cuconasu et al., "The Power of Noise" (SIGIR 2024, arXiv:2401.14887) — semantically-related-but-non-answering distractors degrade as more are added (with a fragile "+irrelevant noise can help" effect later contested — see in-repo 2026-09-08 doc). Net: **recall and answer accuracy are different currencies past a small window; the reader is the bottleneck, and noise is the mechanism.**
- Lost-in-the-middle (TACL 2024) + reader saturation at 20–50 docs (~+1.5%) — in-repo 2026-09-08 doc (https://arxiv.org/abs/2307.03172).

---

## 1. Mem0 (mem0ai) — extraction-based additive memory

### (a) Extraction
`add_memory(messages, user_id, ...)` runs an **LLM extraction pass** over ordered user/assistant turns (`infer=True`, default) pulling "key facts, decisions, preferences" as **flat natural-language memory strings**; `infer=False` stores raw payloads verbatim (docs warn this creates duplicates). Platform `add` auto-pulls **earlier messages sharing user_id/run_id as extraction context** ("Automatic conversation context"), so follow-ups resolve anaphora without resending history. Single-pass **ADD-only** semantics; dedup is an *extraction-time prompt discipline*, not a post-hoc merge step. Sources: https://docs.mem0.ai/core-concepts/memory-operations/add ; https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm ; https://arxiv.org/html/2504.19413v1

### (b) Storage / update semantics
Vector collection of flat records w/ payload (`created_at`, `updated_at`, `expiration_date`, metadata) + a parallel entity collection (platform graph). **ADD-only: memories accumulate; nothing is overwritten or deleted by add.** Explicit `update_memory(id, text)` and `delete_memory(id)` operations exist (platform + OSS). Knowledge updates are handled by (i) users/agents calling UPDATE, (ii) a platform temporal pass tagging memories with `state_key` + setting `event_end` on superseded instances (nothing deleted), and (iii) **Memory Decay / eviction**: a **search-time re-ranking** layer modeled on the Ebbinghaus forgetting curve — recently accessed memories get up to **1.5× boost**, unused damp toward **0.3×**, expired memories are hidden from search unless `show_expired`; Mem0's own admission: "Knowledge-update remains the hardest category for an additive memory architecture" (older semantically-similar facts still rank near newer). Sources: https://docs.mem0.ai/core-concepts/memory-operations/update ; https://docs.mem0.ai/core-concepts/memory-operations/delete ; https://docs.mem0.ai/core-concepts/memory-operations/add ; https://mem0.ai/blog/memory-eviction-and-forgetting-in-ai-agents ; https://github.com/mem0ai/mem0/issues/5330 ; temporal: https://mem0.ai/blog/introducing-temporal-reasoning-in-mem0

### (c) RETRIEVAL → READER assembly (what goes in the prompt)
`search_memory(query, filters, top_k, threshold, rerank)` returns **a flat, score-ranked list of memory strings with metadata and timestamps** — the *calling application* formats them into its prompt (Mem0 ships no reader prompt template; docs/quickstarts inject the returned list as a "memories" context block). Defaults/limits: **top_k default 10, range 1–1000**; `threshold` = minimum similarity; platform `rerank=True` optional (managed cross-encoder catalog); filters (user_id, categories, dates, AND/OR). Their token-efficiency claim: Mem0 reports its pipeline averages **under ~7,000 tokens per retrieval call** (≈6.9K mean across LoCoMo/LongMemEval/BEAM in their own blog) — i.e., the product norm is a small budget of distilled facts injected into the prompt, not raw history. OSS retrieval internals (source-verified in-repo): semantic over-fetch `max(limit*4, 60)` → BM25 + spaCy entity boost as **additive scores on the semantic candidate pool only** (BM25/entity never expand the pool), hard semantic pre-threshold 0.1. Platform graph memory boosts ranking of memories linked to matched entities ("affects ranking, not the response shape"). Platform temporal: query classified by temporal intent (current_state, historical_range, duration_state…) with **no extra LLM call; intent is an additive rerank, never a filter**. No structural/timeline/state rendering — always flat memory strings. Sources: https://docs.mem0.ai/core-concepts/memory-operations/search ; https://docs.mem0.ai/api-reference/memory/search-memories ; https://docs.mem0.ai/platform/features/temporal-reasoning ; https://docs.mem0.ai/platform/features/graph-memory ; https://mem0.ai/blog/the-token-efficient-memory-algorithm-now-has-temporal-reasoning

### (d) Selection / rerank / compression stage between retrieval and model
Yes — three mechanisms: (1) optional **cross-encoder rerank** (platform managed catalog or OSS-configured local/3rd-party) on the over-fetched pool; (2) **temporal-intent additive rerank** (platform); (3) **entity-boost** from graph hits; plus **decay re-scoring** at search time. No learned compression/condensation of memories before injection — token efficiency comes from keeping memories atomic + injecting top-k only. Sources: as (c); https://docs.mem0.ai/features/advanced-retrieval (reranker catalog); https://mem0.ai/blog/memory-eviction-and-forgetting-in-ai-agents

### (e) Benchmarks
- **Self-reported:** LongMemEval overall **93.4%** (Mem0's own Zep-vs-Mem0 comparison, GPT-4o); **94.4% @ top_200** for the temporal-reasoning build (blog: LongMemEval 90.4→94.4 @ top_200; LoCoMo 86.1→92.5; multi-session 82.0→93.2 @ top_50, disclosed open-domain dip −1.9 pts). arXiv:2504.19413 reports token-efficiency/compact-memory results. — **[self-reported]** https://mem0.ai/blog/zep-vs-mem0-which-ai-memory-layer-should-you-choose ; https://mem0.ai/blog/introducing-temporal-reasoning-in-mem0 ; https://arxiv.org/abs/2504.19413
- **Vendor-run 3rd party (competitor, not truly independent):** Vectorize (sells Hindsight, a competing memory system) LongMemEval eval: **Mem0 49.0% vs Zep 63.8%** (GPT-4o). Vectorize self-labels it "independent evaluation"; third-party roundups quote it as "the only clearly independent number found" — but it is a competitor-run comparison with a conflict of interest. https://vectorize.io/articles/mem0-vs-zep

---

## 2. Zep / Graphiti (getzep) — temporal knowledge-graph memory

### (a) Extraction
Write pipeline (LLM-heavy, per fact): extract entities → extract relationships/facts → **separate "date facts" pass** assigning temporal bounds (`valid_at`/`invalid_at`) using episode event time → entity resolution (under-merge over over-merge) → fact resolution (duplicates merge; **contradictions invalidate the older fact**) → entity summaries updated. Raw episodes retained as provenance. In 2026 product: the graph also ingests **Observations** (pattern-matched facts) and builds **thread summaries + user summary**. Sources: https://github.com/getzep/graphiti ; https://help.getzep.com/graphiti/working-with-data/adding-episodes (6-step write) ; https://arxiv.org/abs/2501.13956

### (b) Storage / update semantics
Bi-temporal property graph: Entity nodes, fact **edges** w/ validity windows (`valid_at/invalid_at/expired_at`, observed/recorded provenance), episodic nodes, community/entity summaries. **Invalidation, not deletion** — superseded edges are marked `invalid_at` and remain queryable for "as of t" questions; correctness for "now" queries requires caller-side validity filters in OSS Graphiti (no default filter — source-verified in-repo). 2026: graph service **Konig** (in-memory adjacency lists + CSR matrices + vector/BM25 indexes alongside) powers hot-graph reads. Sources: https://help.getzep.com/graph-overview ; https://help.getzep.com/how-graph-creation-works ; https://blog.getzep.com/why-we-built-a-graph-database-service-for-agent-memory/ (Konig)

### (c) RETRIEVAL → READER assembly (what goes in the prompt)
The flagship read API returns a **rendered "Context Block"** — a prompt-ready string assembled server-side. Two modes (2026):
- **Multi-scope retrieval (legacy/full):** five–six parallel searches across **facts (edges), entities (nodes), episodes, Observations, thread summaries, (user summary)** with fixed per-scope depth (**20 edges / 10 nodes / 10 episodes / 5 thread summaries / 5 observations**) + **cross-encoder reranking**; composed client-side via `thread.get_user_context()`. Median delivered context: **4,408 tokens/question on LongMemEval, 5,760 on LoCoMo** (self-reported, gpt-5.4 reader). Search fuses BM25 + vector + BFS graph traversal (depth 3 default; RRF or MMR λ=0.5 or cross-encoder or node-distance rerankers); episodes are a first-class BM25 scope. Content type in the block = **flat fact strings + entity summaries + episode text** — no timeline/state structure.
- **Smart Context Assembly / Auto Search (new, June 2026):** ranks candidates **across all context types simultaneously** and **packs the block to a character budget (default 2,500 chars; `max_characters` configurable)** — "the block's shape adapts to each query" instead of fixed-per-type counts. ⚠️ Note: the vendor's 2,500-char default and their 2,680-token LoCoMo median are not directly reconcilable (2,500 chars ≈ 600–700 tokens at prose density); the LoCoMo run likely used a larger configured `max_characters`. Budget figures are vendor-stated, not cross-verified. Returns `results.context` (rendered block) and optionally raw structured arrays. Docs: "The Context Block is shaped at retrieval, not generated by an LLM." Read-side LLM calls: **zero** (retrieval = embeddings + BM25 + BFS + reranker; LLM cost is on the write side).
Sources: https://www.getzep.com/research/ ; https://help.getzep.com/retrieving-context ; https://help.getzep.com/working-with-context ; https://help.getzep.com/context-templates ; https://blog.getzep.com/smart-context-assembly-fewer-tokens-higher-quality/ ; https://blog.getzep.com/how-do-you-search-a-knowledge-graph/

### (d) Selection / rerank / compression stage
Strongest in class. **Cross-scope ranking + character-budget packing** (Smart Context Assembly/Auto Search) is an explicit **selection stage between retrieval and the reader**, replacing fixed per-type result limits. Cross-encoder rerankers over candidates; optional MMR for diversity; node-distance rerank for entity-focal queries; temporal validity can be applied as filters. Their own LoCoMo evidence: legacy Context Block = **94.7% @ 5,760 tokens vs Smart Context Assembly = 86.5% @ 2,680 median tokens (−54% tokens for ~8 pts)**; and a separate run where **a smaller assembled block *outscored* a larger one (85% vs 80%)** — vendor evidence that curation-to-budget trades favorably and that excess context is not free. **[self-reported]** https://blog.getzep.com/smart-context-assembly-fewer-tokens-higher-quality/

### (e) Benchmarks
- **Self-reported, evolving:** "up to +18.5% accuracy on LongMemEval vs baseline implementations" (GPT-4o era, ~1.6k tokens, 2.58s median latency — blog) — the widely-cited **71–72%** figure: Zep's own table in their GPT-4.1/o4-mini post reports **72.27% average for the gpt-4o reader**; Mem0's comparison blog also lists **Zep 71.2% (GPT-4o)**. 2026 research page (reader **gpt-5.4** reasoning=medium, judge gpt-5.4 CoT): **LongMemEval 90.2% (451/500)**, by type: single-session assistant 96.4 / single-session user 94.3 / knowledge update 93.6 / temporal 90.2 / preference 90.0 / **multi-session 83.5**; retrieval p50/p95 104/162 ms; **median context 4,408 tokens**; **LoCoMo 94.7% (1,459/1,540)**; LoCoMo auto-search 86.5% @ 2,680 tokens. Also self-corrected an earlier LoCoMo claim to 75.14±0.17 (in-repo prior). Sources: https://blog.getzep.com/state-of-the-art-agent-memory/ ; https://blog.getzep.com/gpt-4-1-and-o4-mini-is-openai-overselling-long-context/ ; https://www.getzep.com/research/ ; https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/
- **Vendor-run 3rd party (competitor):** Vectorize (Hindsight vendor): **Zep 63.8%** (GPT-4o). https://vectorize.io/articles/mem0-vs-zep

---

## 3. Letta (MemGPT lineage) — self-editing memory, no server-side reader assembly

### (a) Extraction
No bulk extraction pipeline: **the agent itself writes memory** via tools. Core memory blocks are LLM/agent-edited; archival memory = the agent inserts passages it chooses (`archival_memory_insert`); conversation history = recall memory. Background **sleep-time agents** (MemGPT 2.0, since v0.7.0) re-read raw context during idle time and **rewrite the in-context memory blocks** ("transforming raw context into learned context"). Sources: https://www.letta.com/blog/agent-memory/ ; https://www.letta.com/blog/sleep-time-compute/ ; https://arxiv.org/abs/2504.13171

### (b) Storage / update semantics
Four-tier hierarchy (docs.letta.com context hierarchy):
- **Memory Blocks** — always in-context, structured, editable sections (label/description/value/character limit); recommended **<50k chars/block, <20 blocks**; edited via `memory_replace`/`memory_insert`/`memory_rethink` tools. This is the "core memory" the reader sees every turn.
- **Files** — up to 5MB, read-only, opened/closed, `semantic_search`/`grep`; <100 files.
- **Archival Memory** — out-of-context vector DB; **300-token chunks returned** per retrieval; unlimited count; accessed only when the agent calls `archival_memory_search`.
- **Recall memory** — searchable conversation history (full-text/vector/hybrid in cloud).
Update semantics = agent-mediated edits; no validity windows, no temporal invalidation layer, no entity typing. Eviction/pressure: enforced by the context manager + **compaction**; memory pressure is the agent deciding what to keep/replace in blocks (MemGPT's core insight: OS-style paging between in-context and archival). Sources: https://docs.letta.com/v1-sdk/memory/context-hierarchy ; https://docs.letta.com/concepts/memfs ; https://arxiv.org/abs/2310.08560

### (c) RETRIEVAL → READER assembly
**No automatic assembly.** Memory blocks are compiled into the system prompt every turn (their contents + block metadata); everything else (archival passages, files, history) enters context **only when the agent decides to retrieve it** via tools. There is no semantic/vector pre-rollup into the prompt by the runtime by default (keyword/hybrid search is optional). Ordering = block order in the system prompt; budget = context-window management + compaction. The reader-LLM is the assembler. Sources: https://docs.letta.com/v1-sdk/memory/context-hierarchy ; https://docs.letta.com/concepts/memfs

### (d) Selection / rerank / compression
**Sleep-time compute** is the consolidation stage: a separate (often stronger) model edits the primary agent's in-context blocks during downtime ("anytime" writes the primary can read immediately), producing "clean, concise, detailed memories" from messy incremental ones — i.e., **compression happens offline and changes exactly what the reader sees next turn** (distilled blocks instead of raw archival). Compaction summarizes conversation when the window grows. Sources: https://www.letta.com/blog/sleep-time-compute/ ; arXiv:2504.13171

### (e) Benchmarks
**[ABSENCE]** No published LongMemEval score from Letta found. Self-reported strength is in memory-management research claims (MemGPT paper; sleep-time compute: Pareto gains on AIME/GSM with load shifted to idle compute — not a memory-QA benchmark). In-repo prior: Zep paper compared DMR 94.8% vs MemGPT 93.4% (their run of MemGPT, self-reported by Zep). Sources: https://arxiv.org/abs/2310.08560 ; https://arxiv.org/abs/2504.13171

---

## 4. LangMem (LangChain) — extraction + hot-path tools over a namespaced store

### (a) Extraction
Two paths: **hot path** = the agent consciously saves memories with `create_manage_memory_tool` during conversation; **background ("subconscious")** = `create_memory_store_manager` LLM extracts memories from conversations after they end. Source: https://langchain-ai.github.io/langmem/hot_path_quickstart/ ; https://langchain-ai.github.io/langmem/background_quickstart/

### (b) Storage / update semantics
LangGraph `BaseStore`, namespaced (e.g., `("memories", user_id)`) semantic collections with an embedding index; items carry timestamps + content. Update/delete **by the agent via manage_memory tool** (create/update/delete by memory ID); background manager can also write. No temporal scoring, no entity graph, no validity invalidation at read. Source: https://langchain-ai.github.io/langmem/hot_path_quickstart/ ; https://deepwiki.com/langchain-ai/langmem/9-api-reference

### (c) RETRIEVAL → READER assembly
Hot-path quickstart is the clearest documented assembly: in the prompt function, `store.search(("memories",), query=<last message>)` (semantic; default limit 10) and the results are **formatted verbatim into the system prompt inside a `## Memories <memories>…</memories>` XML block**; the model then answers with those items above the conversation. Second pattern: `create_search_memory_tool` lets the agent search mid-loop (results = tool output). No rerank/temporal/graph stage; assembly = flat list of memory strings in a fixed XML container. Source: https://langchain-ai.github.io/langmem/hot_path_quickstart/

### (d) Selection / rerank / compression
**[ABSENCE]** No documented rerank/compression/selection stage beyond top-k semantic search + namespace scoping.

### (e) Benchmarks
**[ABSENCE]** No published LongMemEval/LoCoMo number found for LangMem (it is a framework layer, not a benchmarked engine).

---

## 5. basic-memory (basicmachines-co) — file-first personal memory (Markdown + graph)

### (a) Extraction
**File-first**: knowledge lives as Markdown notes with YAML frontmatter; the sync engine parses files into a **semantic graph of entities (documents/notes), observations (categorized facts), relations, and tags**. The LLM writes notes/observations directly (MCP tools; built-in AI); no autonomous chat-memory extraction service. Sources: https://github.com/basicmachines-co/basic-memory ; https://docs.basicmemory.com/reference/technical-information ; https://deepwiki.com/basicmachines-co/docs.basicmemory.com/2.1-system-architecture-and-components

### (b) Storage / update semantics
SQLite (local) or PostgreSQL (cloud) as query index + **Markdown files as source of truth** (git-versionable); relations/observations upserted on file sync; no temporal validity/decay/eviction; user/agent-driven edits. Source: https://docs.basicmemory.com/reference/technical-information

### (c) RETRIEVAL → READER assembly
MCP tool surface the agent calls (no automatic prompt injection): `search_notes` (hybrid FTS + semantic, default 10 results), `read_note`, and **`build_context`** (depth default 2) which **walks the graph to pull related entities + observations around a starting note** — the closest thing to graph-neighborhood assembly in the local-first camp. Output = Markdown rendered back to the agent via MCP. Source: https://deepwiki.com/basicmachines-co/docs.basicmemory.com/2.1-system-architecture-and-components (build_context endpoint; 75% query reduction at v2)

### (d) Selection / rerank / compression
Graph traversal + hybrid scoring only; no cross-encoder/LLM selection stage. **[ABSENCE]** of consolidation/decay.

### (e) Benchmarks
**[ABSENCE]** No LongMemEval claims (personal-knowledge tool, not an agent-memory engine).

---

## 6. supermemory (supermemoryai) — learning-model + graph, "dreaming" consolidation

### (a) Extraction
Two-phase ingest: (1) chunk → **contextual chunking** → embed → index ("done" = chunks searchable); (2) **"dreaming"** — a second pass through their "custom learning model" that **merges, arranges, and links content into graph memories** (facts, updates, relations, time). `dreaming: "dynamic"` groups related documents so memories form from coherent units (production default); `"instant"` dreams each doc alone. Sources: https://supermemory.ai/docs/concepts/how-it-works ; https://github.com/supermemoryai/supermemory

### (b) Storage / update semantics
"Fact-based temporal graph" with vector + FTS + graph built in; container-tag isolation (`containerTag` = user/tenant hard boundary); re-ingest with the same `customId` drives diff-billing updates. No public doc of DELETE/invalidation semantics beyond updates on re-ingest. Source: https://supermemory.ai/docs/concepts/how-it-works

### (c) RETRIEVAL → READER assembly
Three outputs per document: **chunks** (raw grounding), **memories** (graph facts), and a **Profile** — "a sample of memories, static + dynamic summary for **always-on context**." Profiles are injected into the model context **every turn without re-searching** (docs.quickstart: profiles avoid "re-searching the world"); memories/chunks are retrieved on demand via the Search API. Source: https://supermemory.ai/docs/quickstart ; https://supermemory.ai/docs/concepts/how-it-works ; https://supermemory.ai/docs/integrations/opencode (memories fetched and injected at session start)

### (d) Selection / rerank / compression
"Dreaming" is the write-side consolidation that changes reader input (memories + profiles are distilled/structured vs raw chunks); retrieval-side selection details are not publicly documented. Source: https://supermemory.ai/docs/concepts/how-it-works

### (e) Benchmarks
**Self-reported only:** README claims "#1 on every major AI memory benchmark" incl. LongMemEval/LoCoMo/ConvoMem, "95% Recall@15", "99.4% context reduction" — no protocol/methodology published. **[self-reported; LOW confidence]** https://github.com/supermemoryai/supermemory

---

## 7. HippoRAG 1 / HippoRAG 2 and Microsoft GraphRAG — graph-augmented *retrieval*; reader assembly largely unchanged

### HippoRAG 1 & 2 (OSU-NLP; NeurIPS 2024 / NeurIPS 2025)
- **(a) Extraction:** offline, LLM OpenIE → **schemaless KG** (HippoRAG 1: noun-phrase nodes + synonymy edges); HippoRAG 2: phrase (concept) **and passage nodes** joined by "contains" edges.
- **(b) Storage:** KG + offline Personalized-PageRank (PPR) matrix; HippoRAG 2 adds passage-level context nodes.
- **(c) Reader assembly:** at query time: 1-shot LLM extracts query entities/triples → link to KG nodes → **PPR seeded at query nodes** → top-ranked **passages** handed to the reader. **"Uses the top-ranked passages for downstream QA"** — the reader still receives the *original passages*, re-ranked by a graph algorithm. HippoRAG 2 does add one online LLM filtering pass over triples before seeding, and falls back to plain top-k embedding when no triples are extracted.
- **(d) Selection stage:** PPR is itself the selection stage (a graph algorithm replaces iterative retrieval); there is **no reader-side change** — no summarization, no typed slicing, no re-formatting of evidence for the reader.
- **(e) Benchmarks:** self-reported multi-hop gains (+20% class claims v1; +7% associative v2 over SOTA embedders) — **[self-reported]**; error analyses of the family attribute HippoRAG-2 failures to the **graph/triple layer, not the reader** (MemGraphRAG; HippoRAG-2 companion paper — **[in-repo prior]** `docs/research/2026-09-08-graphrag-retrieval-evidence-assembly.md` §1.3); GraphRAG-Bench (independent) found HippoRAG competitive but **not** top (RAPTOR > GraphRAG/HippoRAG). Sources: https://arxiv.org/abs/2405.14831 ; https://arxiv.org/abs/2502.14802 ; https://github.com/OSU-NLP-Group/HippoRAG ; https://arxiv.org/abs/2506.02404
- **Answer for Tortoise:** HippoRAG proves **retrieval can do the multi-hop work** and the reader unchanged; it does not solve the reader-window ceiling (passages still arrive as flat text, PPR-ranked).

### Microsoft GraphRAG
- **(a/b) Index:** chunk → LLM entity+relation extraction → merge/dedup → **Leiden community detection → per-community LLM summaries at every hierarchy level** → embeddings for entities/text units/reports.
- **(c) Reader assembly (local search):** query entities → entity-description embedding match (2× oversample) → fan out to **text units, community reports, entities, relationships** → each candidate list **ranked + filtered to fit a fixed context window** (~12k default tokens; typed proportions: ~50% raw text units, ~10% community reports, ~40% entity/relation descriptions; all-or-nothing item admission). **Global search**: map-reduce over community reports.
- **(d) Selection stage:** yes — entity-anchored expansion + per-type rank/filter to a fixed budget; but index rebuilds are wholesale on update, and community summaries repackage (can bury) specific dated facts.
- **(e) Benchmarks:** self-reported win rates; independent GraphRAG-Bench: moderate gains, not top; DRIFT (self-reported) improves comprehensiveness/diversity for vocabulary-mismatched queries. Sources: https://microsoft.github.io/graphrag/query/local_search/ ; https://microsoft.github.io/graphrag/query/global_search/ ; https://microsoft.github.io/graphrag/index/ ; in-repo 2026-09-08 evidence-assembly doc.
- **Answer for Tortoise:** GraphRAG **does change reader assembly** (typed slices, budget-capped, community summaries as compressed evidence) but for **static corpora, not conversational memory** — and its summary-first compression has documented freshness/rebuild costs that argue against copying it into a live temporal memory (in-repo evidence-assembly doc, implications 6–8).

---

## 8. Memory consolidation that changes WHAT THE READER SEES (vs raw retrieval)

Consolidation ≠ retrieval improvement: it **rewrites the evidence store so subsequent reads present different content**. Documented families, with the reader-input change each makes:

1. **Agent/offline reflection at "sleep time"** — Letta sleep-time agents rewrite in-context blocks (see §3d); Generative Agents' periodic "reflection" synthesizes higher-level insights from observations into the memory stream (arXiv:2304.03442); Mem0's platform temporal pass annotates state/event metadata asynchronously post-write (write-path metadata, read-path rerank). Effect on reader: **distilled/curated blocks replace raw passages.** Sources: https://www.letta.com/blog/sleep-time-compute/ ; arXiv:2504.13171 ; arXiv:2304.03442 ; https://mem0.ai/blog/introducing-temporal-reasoning-in-mem0
2. **Reflexion-style episodic self-reflection** (NeurIPS 2023) — task failures are converted to *verbal* self-reflections stored in an episodic buffer and **added as additional context for the next attempt**; the reader literally sees "last attempt + critique" rather than raw logs. This is the purest demonstration that **added distilled reflection changes reader input more than added raw evidence** — and that self-summarized critique is the semantic gradient signal, not more data. Sources: https://proceedings.neurips.cc/paper_files/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf ; https://github.com/noahshinn/reflexion
3. **Decay / forgetting as reader-relevant curation** — Mem0 Memory Decay re-weights at search time (Ebbinghaus-shaped; 0.3×–1.5×); proposed lifetime tracking + 14-day grace + threshold deletion (issue #5330). Effect: the window skews to recently-reinforced facts. Sources: https://mem0.ai/blog/memory-eviction-and-forgetting-in-ai-agents ; https://github.com/mem0ai/mem0/issues/5330
4. **Summarization hierarchies** — RAPTOR-style recursive summarization + GraphRAG community reports + supermemory's "dreaming"/profiles give the reader **multi-granularity summaries as coarse-to-fine context** (DRIFT primer→refine is the read-side exploit of this). Costs: wholesale rebuild on update, fact-repackaging/burial risk (in-repo 2026-09-08 doc). Sources: https://microsoft.github.io/graphrag/ ; https://supermemory.ai/docs/concepts/how-it-works ; in-repo prior.
5. **Selective retention / consolidation frameworks (practitioner surveys)** — Mnemoverse, Cognilium, ApX-ML summarize the pattern: extract durable facts → summarize narrative → **reconcile contradictions at write** → let stale memory decay → periodic reflection passes. Effect: fewer, better items in the reader window. Sources: https://mnemoverse.com/docs/library/agent-memory-consolidation ; https://cognilium.ai/blogs/agent-memory-consolidation ; https://apxml.com/courses/agentic-llm-memory-architectures/chapter-3-designing-memory-systems/memory-consolidation-summarization

**Consolidation answer for Tortoise:** yes — every vendor that consolidates does so to change what the reader sees (blocks, profiles, summaries, dated instances), not merely to retrieve more. But note the split: **distilled-fact consolidation (Mem0/Letta/supermemory profiles) trades away the dated evidence Tortoise needs for temporal/state questions**, while **invalidation-preserving consolidation (Zep validity windows, Tortoise's own supersession) keeps history and projects state** — the safer shape for an epistemic graph.

---

## 9. Comparison table

| System | Extraction | Storage / update semantics | Recall → reader assembly (what the prompt gets) | Selection stage between retrieval & model | Benchmarks (LongMemEval) |
|---|---|---|---|---|---|
| **Mem0** | LLM extracts flat NL memory strings from turns (add, infer=True); platform auto-pulls prior turns as context | ADD-only accumulation; explicit update/delete ops; state_key + event_end for states; Memory Decay (Ebbinghaus re-rank 0.3×–1.5×); expiration_date | Flat top-k memory strings w/ metadata; top_k default 10 (1–1000); app formats into prompt; ~7k-token injection guidance; temporal intent = additive rerank | Optional cross-encoder rerank; entity-boost; decay re-score. No compression of items | Self: **93.4%**; **94.4%@top_200** temporal build. Vendor-run 3rd party (Vectorize, competitor): **49.0%** |
| **Zep / Graphiti** | LLM write pipeline: entities → facts → **date pass** → resolve/merge → invalidate contradictions → entity summaries; Observations + thread summaries (2026) | Bi-temporal property graph; invalidation not deletion (validity windows); Konig in-memory graph service | Rendered **Context Block**: 6 scopes (facts/entities/episodes/Observations/thread/user summaries) → flat facts + entity summaries; or **Auto Search**: cross-scope rank → pack to 2,500-char budget; median 4,408 tok/question | **Cross-scope rerank + character-budget packing** (best-in-class); cross-encoder/MMR/node-distance; caller validity filters | Self: 71–72% (gpt-4o era); **90.2%** (gpt-5.4, 451/500). Vendor-run 3rd party (Vectorize, competitor): **63.8%** |
| **Letta / MemGPT** | Agent self-writes via tools; no bulk extraction; sleep-time agents re-read + rewrite blocks | 4-tier: in-context blocks (<50k chars), files, archival passages (300-tok chunks), recall history; agent-mediated edits; compaction | Memory blocks compiled into system prompt every turn; archival/recall entered **only when agent retrieves**; no runtime auto-assembly | Sleep-time compute rewrites blocks offline (compression); context manager paging/eviction; compaction | **[ABSENCE]** no published LongMemEval score |
| **LangMem** | Hot-path tools + background "subconscious" LLM extraction | LangGraph BaseStore namespaced semantic items; agent manage-memory (create/update/delete) | `store.search` results verbatim in system prompt `## Memories <memories>…</memories>`; default top 10; or agent tool mid-loop | **[ABSENCE]** none documented (top-k + namespace only) | **[ABSENCE]** no published score |
| **basic-memory** | File parsing → entities/observations/relations/tags (Markdown-first; LLM writes via MCP) | SQLite/Postgres index + Markdown source of truth; no validity/decay | Agent-driven MCP reads: search_notes (10 default), read_note, build_context (graph walk depth 2) → Markdown | Graph traversal + hybrid scoring; no rerank/compression | **[ABSENCE]** (not an agent-memory engine) |
| **supermemory** | Chunk → contextual-chunk → embed; **"dreaming"** learning-model pass merges into graph facts | Fact-based temporal graph (vector+FTS+graph); container isolation; customId re-ingest updates | Chunks + memories on demand; **Profile (static+dynamic summary) injected every turn** | Dreaming (write-side consolidation) changes reader input; retrieval selection not documented | Self: "#1 on every major benchmark", "95% Recall@15", "99.4% context reduction" — no methodology **[LOW]** |
| **HippoRAG 1/2** | OpenIE triples → schemaless KG (+passage nodes in v2) | KG + offline PPR matrix; no update/invalidation semantics (corpus tool) | Top-**passages** (originals) PPR-ranked → reader unchanged | PPR = graph selection stage replacing iterative retrieval; v2 adds LLM triple filter + embedding fallback | Self: multi-hop +20% class claims; no independent LongMemEval-style run on conversational memory |
| **MS GraphRAG** | LLM entity+relation extraction → communities → hierarchical LLM summaries | Static corpus; wholesale rebuild on update | Local: typed slices (text units ~50% / reports ~10% / entity+rel ~40%) packed to fixed ~12k-token window; Global: map-reduce over reports | Entity-anchored expansion + per-type rank/filter to budget; no temporal layer | Self win-rates; independent GraphRAG-Bench: mid-tier (RAPTOR > GraphRAG/HippoRAG) |

---

## 10. Synthesis — how do competitors assemble evidence for the reader, and does anyone solve "more recall hurts accuracy"?

### What the field actually does (assembly patterns, 2026)
1. **The dominant product pattern is "small curated fact lists," not large raw windows.** Mem0 (~top-10 memories, ~7k-token guidance), Zep (median 4.4k tokens LongMemEval; 2,500-char budget in Smart Context Assembly), LangMem (top 10 in an XML block), supermemory (profiles + small memory sets), Letta (blocks + on-demand reads). Everyone converged on **tens of items / low-thousands of tokens** — the same order of magnitude as Tortoise's ~40-item window. Nobody floods the reader.
2. **The two structural answers to the ceiling are (i) selection/compression between retrieval and reader, and (ii) write-time consolidation so reads present distilled or state-projected content — not raw chunks.** Zep's Auto Search (cross-scope rank → character-budget pack) and GraphRAG's local typed-slice budget are (i); Letta sleep-time, Mem0 state_key/decay, supermemory dreaming/profiles, Graphiti entity summaries and write-time invalidation are (ii).
3. **Recall is treated as a means, and every credible system now publishes the gap between retrieval and end-to-end as the real battleground** — Zep's research page reports retrieval latency/context size *alongside* accuracy; the Aingram retrieval-only run makes the ceiling explicit.

### Does anyone solve the "more recall doesn't improve (or hurts) accuracy" problem?
**Not by reader architecture alone — no production system has a documented "reader-side" fix that decouples accuracy from window size.** What exists, by mechanism:

- **Zep comes closest in product form**: Smart Context Assembly explicitly *shrinks and re-shapes* context per query and reports **token-efficiency at near-par accuracy** (86.5% @ 2,680 tok vs 94.7% @ 5,760 tok) and one run where a *smaller* block outscored a *larger* one (85 vs 80) — direct vendor evidence that **excess assembled context carries noise, and curation-to-budget is a quality lever, not just a cost lever**. **[self-reported]**
- **The mechanism is documented in independent RAG literature**: added evidence beyond a small window adds *precision-destroying distractors* (arXiv:2410.05983, 2503.00353, 2506.03100, 2401.14887), and reader performance is positional and saturating (lost-in-the-middle). So the "added evidence can degrade answers" effect is **real and external**, and the field's countermeasure is **precision-oriented selection + small budgets**, exactly what the top systems ship.
- **Consolidation systems solve it by changing what counts as evidence**: the reader sees *reflections* (Reflexion), *rewritten blocks* (Letta sleep-time), *profiles/summaries* (supermemory), or *state-projected dated instances* (Mem0 state_key; Zep validity windows) instead of raw retrieval — i.e., **they move the burden from reader-time noise-tolerance to write-time distillation**. But fact-distillation trades away the dated evidence needed for temporal/multi-event questions (the categories Tortoise struggles with and where Mem0's own gains came only when dated instances co-retrieved).
- **HippoRAG/GraphRAG show retrieval-side-only gains (better passages in, same flat reader) top out**: independent GraphRAG-Bench finds structural-context methods can *degrade* QA below no-retrieval, and HippoRAG-2 error analyses blame the graph layer, not the reader.

### Bottom line for Tortoise
The measured phenomenon (recall gains do not transfer; a ~40-item window caps benefit; added evidence can hurt) is **the field's norm, not a Tortoise defect**. The competitors who look best on it do three things Tortoise can mirror without copying their data model:
1. **Selection between retrieval and reader** (Zep Auto Search, GraphRAG local typed budgets): rank across evidence types, then **pack to a fixed budget by type with all-or-nothing item admission** — never count-based truncation of items.
2. **Write-time state projection + invalidation instead of raw-fact accumulation** (Zep validity windows; Mem0 state_key; Tortoise's own supersession/EP): the reader should see *why* a fact is stale, and current state should be projected, not inferred from competing instances.
3. **Dedup/quality-over-size before rendering** (DEG-RAG, arXiv:2510.14271: removing ~40–50% of duplicate entities/relations *improves* QA across four graph-RAG variants — see in-repo `docs/research/2026-09-08-graphrag-retrieval-evidence-assembly.md` §3.2; Zep's smaller-block-beats-larger run): if extra recall adds duplicates or near-duplicates, drop them *before* the reader — which converts "more recall" into "better precision."
No competitor yet ships a deterministic entity→state→timeline→evidence assembler (Tortoise's design) — the closest verified parts are Zep's scoped budgets + Smart Context Assembly ranking, GraphRAG's typed-slice window, and Mem0's additive intent rerank. That structured-block reader remains an open differentiator, and the evidence above says it is the *right* layer to fix the ceiling — provided it packs to a hard typed budget and prunes noise/duplicates before the single reader call.

---

## 11. Source confidence summary

| Claim | Tier | Sources |
|---|---|---|
| Vendor LongMemEval self-reports exceed third-party evals by ~8–45 pts (era/reader-dependent) | HIGH | mem0 blog vs vectorize (vendor-run); zep research page vs vectorize; in-repo 2026-09-02 |
| Mem0 search returns flat top-k memory strings; app formats them; top_k 10 default | HIGH | mem0 docs (primary, fetched) |
| Mem0 ADD-only + state_key + decay (0.3×–1.5× Ebbinghaus re-rank) | HIGH | mem0 docs/blog/issues (primary) |
| Zep Context Block = multi-scope search → cross-scope rank → char-budget pack; 6 scopes; 2,500-char default | HIGH | zep research page + smart-context-assembly blog + help docs (primary, fetched) |
| Zep Smart Context Assembly: 86.5%@2,680 tok vs 94.7%@5,760 tok LoCoMo (self) | MEDIUM (self-reported, single vendor) | zep blog |
| Zep LongMemEval numbers: 71–72% (gpt-4o era) → 90.2% (gpt-5.4, 2026) | MEDIUM (self-reported; multiple vendor pages agree on era numbers) | zep blogs + research page; mem0 comparison blog |
| Vectorize Mem0 49.0 / Zep 63.8 — competitor-run (Hindsight vendor), not truly independent | MEDIUM ⚠️ conflict-of-interest | vectorize.io/articles/mem0-vs-zep (Hindsight vendor) |
| Letta: blocks in system prompt; archival 300-tok chunks on demand; no published LongMemEval | HIGH (docs) / ABSENCE (benchmark) | letta docs + blog |
| LangMem: `<memories>` XML injection of store.search top-10 | HIGH | langmem hot-path quickstart (primary, fetched) |
| basic-memory: Markdown-first entities/observations; build_context depth-2 walk; no benchmark claims | HIGH | basicmemory docs + deepwiki (primary, fetched) |
| supermemory: dreaming consolidation + always-on profiles; benchmark claims self-reported w/o methodology | MEDIUM (mechanism, vendor docs) / LOW (numbers) | supermemory docs + GitHub README |
| HippoRAG/2 + GraphRAG change retrieval, not the reader unit (top passages / typed slices) | HIGH | arXiv papers + microsoft docs + independent GraphRAG-Bench |
| Recall ceiling ≫ end-to-end accuracy on LongMemEval (oracle vs noisy split) | MEDIUM ⚠️ single vendor (Aingram) but consistent with oracle-metadata design + paper framing | dev.to (Aingram); arXiv:2410.10813 |
| More/noisy retrieved evidence degrades LLM answers | HIGH (multiple independent papers) | arXiv 2410.05983, 2503.00353, 2506.03100, 2401.14887 |

## 12. Absence register (checked, not found)
- No *truly* independent (non-vendor-affiliated) academic eval of Mem0/Zep on LongMemEval found in this session (Vectorize is a competitor — see §1e/§2e; Digital Applied's roundup likewise found no cleaner number — carried from in-repo 2026-09-02 doc; roundup: https://digitalapplied.com/blog/open-source-agent-memory-mem0-letta-zep-compared).
- No published Letta or LangMem LongMemEval score.
- No public doc of supermemory's retrieval-side selection or benchmark methodology.
- No production system documents a deterministic entity→state→timeline→evidence reader assembly (consistent with in-repo 2026-09-08 findings).

## 13. Sources consulted (load-bearing URLs)
- LongMemEval: https://arxiv.org/abs/2410.10813 ; retrieval-only ceiling: https://dev.to/bozbuilds/perfect-retrieval-recall-on-the-hardest-ai-memory-benchmark-running-fully-local-5dhc
- Mem0: https://docs.mem0.ai/core-concepts/memory-operations/{add,search,update,delete} ; https://docs.mem0.ai/api-reference/memory/search-memories ; https://docs.mem0.ai/platform/features/{graph-memory,temporal-reasoning} ; https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm ; https://mem0.ai/blog/the-token-efficient-memory-algorithm-now-has-temporal-reasoning ; https://mem0.ai/blog/introducing-temporal-reasoning-in-mem0 ; https://mem0.ai/blog/memory-eviction-and-forgetting-in-ai-agents ; https://mem0.ai/blog/zep-vs-mem0-which-ai-memory-layer-should-you-choose ; https://github.com/mem0ai/mem0/issues/5330 ; https://arxiv.org/abs/2504.19413
- Zep/Graphiti: https://www.getzep.com/research/ ; https://help.getzep.com/retrieving-context ; https://help.getzep.com/working-with-context ; https://help.getzep.com/context-templates ; https://help.getzep.com/graph-overview ; https://help.getzep.com/how-graph-creation-works ; https://github.com/getzep/graphiti ; https://blog.getzep.com/smart-context-assembly-fewer-tokens-higher-quality/ ; https://blog.getzep.com/how-do-you-search-a-knowledge-graph/ ; https://blog.getzep.com/state-of-the-art-agent-memory/ ; https://blog.getzep.com/gpt-4-1-and-o4-mini-is-openai-overselling-long-context/ ; https://blog.getzep.com/why-we-built-a-graph-database-service-for-agent-memory/ ; https://arxiv.org/abs/2501.13956
- Letta: https://docs.letta.com/v1-sdk/memory/context-hierarchy ; https://docs.letta.com/concepts/memfs ; https://www.letta.com/blog/agent-memory/ ; https://www.letta.com/blog/sleep-time-compute/ ; https://arxiv.org/abs/2504.13171 ; https://arxiv.org/abs/2310.08560
- LangMem: https://langchain-ai.github.io/langmem/hot_path_quickstart/ ; https://langchain-ai.github.io/langmem/background_quickstart/ ; https://github.com/langchain-ai/langmem
- basic-memory: https://github.com/basicmachines-co/basic-memory ; https://docs.basicmemory.com/reference/technical-information ; https://deepwiki.com/basicmachines-co/docs.basicmemory.com/2.1-system-architecture-and-components
- supermemory: https://supermemory.ai/docs/concepts/how-it-works ; https://supermemory.ai/docs/quickstart ; https://supermemory.ai/docs/integrations/opencode ; https://github.com/supermemoryai/supermemory
- HippoRAG/GraphRAG: https://arxiv.org/abs/2405.14831 ; https://arxiv.org/abs/2502.14802 ; https://github.com/OSU-NLP-Group/HippoRAG ; https://microsoft.github.io/graphrag/query/local_search/ ; https://microsoft.github.io/graphrag/query/global_search/ ; https://arxiv.org/abs/2506.02404 (GraphRAG-Bench)
- Noise/reader literature: https://arxiv.org/abs/2410.05983 ; https://arxiv.org/abs/2503.00353 ; https://arxiv.org/abs/2506.03100 ; https://arxiv.org/abs/2401.14887 ; https://arxiv.org/abs/2307.03172 (lost-in-the-middle)
- Consolidation: https://proceedings.neurips.cc/paper_files/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf (Reflexion) ; https://github.com/noahshinn/reflexion ; https://mnemoverse.com/docs/library/agent-memory-consolidation ; https://cognilium.ai/blogs/agent-memory-consolidation ; https://apxml.com/courses/agentic-llm-memory-architectures/chapter-3-designing-memory-systems/memory-consolidation-summarization ; https://arxiv.org/abs/2304.03442 (Generative Agents)
- Independent/vendor-run comparisons: https://vectorize.io/articles/mem0-vs-zep ; https://www.emergence.ai/blog/sota-on-longmemeval-with-rag
- In-repo prior (read-side source-verified): `docs/research/2026-09-08-graphrag-retrieval-evidence-assembly.md` ; `docs/research/2026-09-08-connected-assembly-read-paths.md` ; `docs/research/2026-09-02-temporal-reasoning-competitors.md`
