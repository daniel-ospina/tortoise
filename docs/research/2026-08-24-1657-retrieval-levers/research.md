# Research Brief — #1657: Retrieval Levers (Key-Expansion, Time-Aware QE, Fusion-Fix, TF-IDF Hard-Tier Hybrid)

**Issue:** #1657 · **Team:** epistemic-team · **Complexity:** standard · **Status:** scoping research (Phase 1.5 artifact)
**Date:** 2026-08-22 · **Repo read:** `/private/tmp/tw-1349` (post-#1349, bge-small shipped)
**Baseline (issue, authoritative):** hybrid arm, LongMemEval-S, Docker/HNSW surface — turn_recall@10 = **0.786**, nDCG@10 = **0.598** (bge-small-en-v1.5)

> This is NOT #317 (cross-encoder reranking). CE CPU latency ≈ +210ms/top-10 (docs/research/2026-08-13-317-cross-encoder-reranking.md) is incompatible with the 300ms E2E budget unless GPU/API-served. Every lever below is sub-10ms CPU.

---

## 0. The retrieval stack being levered (verified in code)

| Layer | File | Current mechanism |
|---|---|---|
| Query classify | `tortoise/search_engine.py::classify_query` | text query → all 3 strategies (FTS + vector + structural) |
| FTS leg | `run_fts_query` | FalkorDB `db.idx.fulltext.queryNodes` over `build_or_query(query)` — R2 #1541 OR-union tokenizer (`tortoise/sparse.py`, sort-by-length, max 12 terms, stopword/digit filtering) |
| Vector leg | `run_vector_query` | HNSW `queryNodes` (Docker, sig-A/B both supported) or brute-force euclidean (embedded); score = cosine clamped [0,1] or rank-based |
| Structural leg | `run_structural_query` + `expand_structural_hops` | kind-scan (score 1.0) + R4 1–2 hop IMPL/NAND expansion (hop-1=1.0, hop-2=0.5) seeded from FTS+vector hits only |
| Fusion | `rrf_fusion(ranked_lists, k=60)` | **Equal-weight Σ 1/(k+rank)** — k=60 Cormack default; R5 recency multiplier `×(1+recency_boost×w)` optional |
| Degradation | `degradation_chain` | 3 strategies parallel, 500ms collective cap, per-strategy circuit breakers |
| TF-IDF fallback | `fallback_tfidf` + `fallback_snapshot` (#1375) | last-resort ONLY when ALL strategies fail; indexed text = `index_text(content, search_keys)`; snapshot kills the ~350ms refetch+fit |
| Eval harness | `tools/longmem_eval/retrieve.py` + `run.py` | `hybrid_search` → `tortoise_fts_query` (structural_kind=statement, hops=2, include_terminal, TR recency) | `_vector_retrieve` (#1349 vector arm, nDCG/P/ranked-ids) |
| TR handling (R5 #1544) | `retrieve.py::detect_time_constraint/_apply_time_window` | TR questions: events in pool, `recency_boost=0.5` on createdAt/startedAt, interval/recency hard window filters with fallback, time-ascending rendering |
| Ingest alias hook | `ingest.py` / `sdk.py::_flatten_search_keys_prop` | **`search_keys` written per point at ingest** (v2/v3 extractor aliases); indexed via `content ∪ search_keys`; E3 #1535 annotates hits with `search_keys` for a *future query-expansion consumer* — **nothing consumes it at query time yet** |

**Relevant committed evidence:**
- `tests/eval/retrieval/baseline/baseline-embedded-2026-08-17.json` (synthetic topic-centroid vectors, embedded surface): vector nDCG@10 = **0.8546**; **fused = 0.835 < vector — paired delta −1.95 nDCG pts, 90% CI [−3.25, −0.83]**; FTS nDCG@10 = 0.382. Fusing FTS+structural *diluted* the near-ceiling vector leg.
- `docs/research/2026-08-17-1349-embedder-selection/evidence/bge-small.json` (real bge-small, HNSW, **vector arm only**): turn_recall@10 = 0.7294, nDCG@10 = 0.5649, session_recall@10 = 0.9207, retrieval p50 = 2.42ms / p95 = 28.9ms.
- Hybrid baseline (issue): 0.786 / 0.598 → on the real surface hybrid > vector-only on both metrics (+0.057 turn_recall, +0.033 nDCG). The vector leg is *under-weighted but still additive* — the lever is to lift the hybrid above both, not to drop legs.
- v2 retrieval report (`docs/drafts/2026-08-20-extractor-v3-category-reports/lme-v2-retrieval-report.md`, probe C): a paraphrased evidence point ("go-to board game" vs query "favorite board game") is **zeroed by strict-AND FTS, surfaced at rank 1 by TF-IDF cosine, rank 3 by OR-tolerant subset query**. Sparse leg is the paraphrase bottleneck; vector leg is the rescue surface for paraphrased points.

---

## Lever 1 — Key-expansion (synonyms/paraphrases before embedding)

### Current mechanism
Query is embedded as-is (`EmbeddingModel.encode([query])` in `sdk.tortoise_fts_query` / `_encode_query_vec`). The only lexical tolerance in the pipeline is `build_or_query`'s OR-union on the FTS leg — it unions *query tokens*, it does not add *new tokens*. `search_keys` (extractor-written aliases) are indexed into points but never used to expand the query.

### Hypothesis
Vocabulary-mismatched queries (LongMemEval IE category: "favorite" vs stored "go-to") miss the dense leg's neighborhood because the query embedding is computed over one phrasing. Expanding the query with (a) entity/alias terms harvested from the *retrieved pool* (pseudo-relevance feedback on `search_keys`), or (b) 1–3 paraphrase embeddings, widens the candidate pool. Per the EACL-2024 finding, expansion helps *weaker* retrievers (bge-small is a 33M-param bi-encoder — weak relative to rerankers), so the direction is favorable here.

### Expected cost/benefit
- **Cost (production, 300ms budget):** FTS-side expansion = string op, ~0ms. Dense-side: 1 extra embed ≈ 1–3ms CPU (bge-small ~1.7–2× MiniLM per encode); 3–5 paraphrase embeds ≈ 5–15ms. **LLM-based expansion (HyDE/paraphrase generation) is NOT budget-compatible in the hot path** — needs a local model or deferred/offline variant. Budget-compatible subset: rule-based search_keys PRF + static synonym map.
- **Benefit (expected):** recall lever, mostly on the *sparse* leg. SemEval-2026 Task 8 (multi-turn conversational retrieval): HyDE on BM25 +26.7% nDCG@10 vs dense +4.0% — "dense retrievers already capture much of the semantic information that HyDE provides". The dense leg's synonymy gap is already partially bridged by bge-small's training. **Realistic ceiling: turn_recall@10 +1–3 pts on IE-ish vocabulary-mismatch questions; nDCG mostly flat.**
- **Pitfall (ACL 2025 findings):** HyDE-style gains on benchmarks correlate with *knowledge leakage* (the generator reproduces gold evidence from pretraining). LongMemEval-S conversations are synthetic per-user — leakage risk is low but the reported HyDE deltas are inflated vs. real-world niche content. Also: "keyword simplification hurts (−11–28%)" — a naive synonym-map that *replaces* tokens regresses; expansion must be *additive* and preserve the original query.

### External precedent
- **mem0 v3**: no LLM query expansion in search. Preprocess = `lemmatize_for_bm25(query)` + `extract_entities(query)` (spaCy, optional). Entities are matched against a dedicated entity store (boosts memories sharing query entities) — i.e., *entity-level* key expansion, not paraphrase generation. `ENTITY_BOOST_WEIGHT` additive. (mem0 docs; mem0ai/mem0#4805)
- **Zep/graphiti**: no query expansion either — 4 scopes × (BM25 + vector + BFS), RRF fusion; query is used as-is. The graph (node/edge) scopes play the "expansion" role (entities reached by traversal). (getzep/graphiti search.py; DeepWiki)
- **LangMem (LangChain)**: server-side OpenAI embeddings; no published query-expansion pass in retrieval.
- **Academic:** HyDE (Gao 2022) — generate hypothetical answer doc, embed it; strong on Contriever/BM25, weaker marginal on strong dense. Query2doc, CoT-expansion, MuGI (feature-pooling of generated passages, +19.8% BM25/+7% dense on TREC-DL with ChatGPT + A100). Rocchio/PRF/RM3 = classical local expansion.
- **Verdict for Tortoise:** the highest-value, budget-compatible variant is **PRF over `search_keys`** (aliases the extractor already wrote) + FTS-term injection — the exact "R2 consumer" E3 #1535 anticipated. HyDE is a *research-surface* option (eval-layer only) to size the headroom, not a prod lever.

**Confidence:** medium (`⚠️ emerging` — external deltas are benchmark-inflated; the in-repo probe C + search_keys hook is the strongest signal, and it favors the FTS leg, not the dense leg).

---

## Lever 2 — Time-aware query expansion (query-side temporal injection)

### Current mechanism
R5 #1544 already ships the *retrieval-side* temporal stack: TR questions get (a) `recency_boost=0.5` multiplier inside RRF (`recency_field=createdAt/startedAt` → `_recency_factors` rank-percentile), (b) `detect_time_constraint` (interval/recency/ordering) → hard window filter `_apply_time_window` with a never-starve fallback, (c) events in the pool, (d) time-ascending context rendering. This is gated on `question_type == "temporal-reasoning"` only. **The query-side variant — injecting the question date ("Current Date: …" / "as of {date}") into the query string before embedding — does not exist.** `question_date` is already available per question and prepended only to the *reader context* header.

### Hypothesis
Two sub-gaps:
1. **Temporal intent outside the TR category.** Knowledge Updates (72 Q in LongMemEval-S: "has the user changed their mind about X?") has no recency handling — retrieval must prefer the *latest* version, and the graph already has CORRECTS/superseded_by/valid_from-to for the reader to discount, but retrieval ordering is blind to it. mem0/AutoMem/temporal-rag all treat temporal intent as *query-detectable* independent of a question-type label.
2. **Query-side date anchoring.** mem0's `reference_date` and the official LongMemEval gen.py `Current Date:` header exist because "as of {date}" changes the correct answer; bge-small embeddings of the bare question cannot express "which version was current on X".

### Expected cost/benefit
- **Cost:** near-zero. String injection before embed (~0ms) + a rule-based temporal-intent classifier (already exists as `detect_time_constraint` — extend its trigger set, no new deps). One extra embed at most.
- **Benefit (expected):** bounded. TR category already handled (sr@5 = 0.786 baseline; the R5 knobs shipped and are measured). The measurable delta is KU-category recency + date-anchored TR intervals. External precedent: mem0's temporal score is *additive and semantic-dominated* ("nudges ranking without filtering candidates out" — exactly the R5 fallback posture); temporal-rag's adaptive `temporal_weight` ("current"→0.70, baseline→0.20) and AutoMem's `RECALL_RECENCY_BIAS=auto` (temporal queries only, default off) both say: **apply recency only when the query expresses temporal intent**. The "invert recency for 'three months ago'" rule (Jatin Bansal; mem0/Letta temporal-intent flags) is the failure mode to avoid — an explicit-date query must NOT get the fresh-biased weight.
- **Risk:** the R5 machinery already proved the never-starve fallback; the query-side variant inherits it. Risk is low; so is the ceiling.

### External precedent
- **mem0 v3**: Temporal Reasoning = separate write-time metadata pass (event/state/plan/preference/relationship/absence, precision, ongoing/completed) scored at read time against the query's temporal intent — *no extra LLM call at search*; `reference_date` param. Additive score; semantic dominates.
- **AutoMem**: `SEARCH_WEIGHT_RECENCY=0.10` age decay (always on) + `RECALL_RECENCY_BIAS=auto` relative re-rank (only when query expresses temporal intent); `RECALL_RELEVANCE_GATE` damps off-topic-but-important memories — a within-pool relevance floor so recency/importance cannot ride an irrelevant memory to the top.
- **temporal-rag (emmimal)**: validity filter hard-removes EXPIRED; EVENT relevance gate = raw cosine floor so freshness cannot override relevance; adaptive temporal_weight by query phrasing; ~15–30ms for a 20-doc temporal rerank (well inside the 300ms budget).
- **Re3 (arXiv 2509.01306)**: learnable query-aware gating between semantic and temporal scores; missing-aware encoding; fixed-weight fusion demonstrably degrades on hybrid temporal queries. (Academic ceiling; not budget-relevant.)
- **MemPalace PR #1425 (in-repo family)**: exponential-decay recency, half-life 30d, default-off no-op — the same default-off contract R5 uses.

**Confidence:** medium-low for the *query-side* variant specifically (`⚠️ single-source` — the query-side date injection is novel; the retrieval-side R5 stack is shipped and measured, so the marginal lever is small). The **high-value sub-lever is extending temporal intent to non-TR categories (KU)** — that is in-repo measurable.

---

## Lever 3 — Fusion-fix (RRF weights/order)

### Current mechanism
`rrf_fusion` sums `1/(k+rank)` with **k=60 and equal weight across FTS/vector/structural**. The eval's `hybrid_search` merges per-entity calls by RRF score. The only knobs are k and the R5 recency multiplier. There is no per-leg weight, no per-leg k, no conditional leg gating.

### Hypothesis
The strongest leg (vector, post-swap) is under-weighted: a point that ranks #1 in vector but rank ~15 in FTS gets `1/61 + 1/75 ≈ 0.0297`, while a point ranked ~8 in both legs gets `2/68 ≈ 0.0294` — consensus beats a single strong signal. In-repo evidence: on the synthetic-centroid surface, fused (0.835) < vector-only (0.8546), paired delta **−1.95 nDCG pts [−3.25, −0.83]** — equal-weight fusion *cost* ~2 points vs the best leg. On the real bge-small HNSW surface, hybrid (0.786/0.598) > vector-only (0.7294/0.5649) — so legs are still additive, but the mechanism (dilution of the strongest leg) is the same; a weight sweep should lift the hybrid above both.

### Expected cost/benefit
- **Cost:** ~0ms — pure post-fusion arithmetic. The only architecture surface is `rrf_fusion`'s signature (add `leg_weights: dict[str,float] | None`, default None = byte-identical) + one knob thread-through in `tortoise_fts_query` / `hybrid_search` (mirror the R5 recency_boost pattern).
- **Benefit (expected):** the **highest evidence-to-cost ratio of the four levers**. The paired delta on the embedded surface is already measured (−1.95 pts at equal weights → the weight sweep is the fix direction). Expected: recover a meaningful share of the dilution gap; nDCG@10 is the metric that moves most (ordering), turn_recall@10 moves less (pool membership changes only at leg-boundary ranks).
- **Pitfalls (external):**
  - "Don't reach for weights until you've measured" (BigDataBoutique/OpenSearch guidance) — **we have measured**: paired judged deltas + the real-surface hybrid-vs-vector split are exactly the "one retriever should dominate" evidence OpenSearch says justifies weighted fusion.
  - OpenSearch's six-dataset benchmark: RRF slightly *behind* weighted min-max normalization on raw NDCG@10 but **immune to score-distribution drift** — if weights are tuned on this surface they must be re-checked when the embedder or corpus changes (the RRF-insensitivity is the reason vendors default to it). Mitigation: keep weights as config/env knobs (like recency_boost), pre-registered, re-measured on the same surface.
  - Redis blog: RRF cannot distinguish a #1 scoring 0.99 from a #1 scoring 0.51 — with a *strong* vector leg, rank-only fusion discards the cosine magnitude. A score-aware alternative (min-max normalized cosine + weighted sum) is the alternative approach (see scoping.md), but RRF's scale-invariance is a feature for a multi-leg pipeline where FTS scores are unbounded.
  - k is "not critical" (Cormack: near-optimal 40–80; the paper says k=60 "was not altered during validation") — a k×weight joint sweep is cheap and should be in the plan.

### External precedent
- **Cormack et al. 2009 (SIGIR)**: k=60 fixed in pilot, "choice was not critical"; RRF "almost invariably improved on the best of the combined results"; beat Condorcet/CombMNZ/learned LTR baselines.
- **Weighted RRF variants in production**: MongoDB `$rankFusion` exposes per-retriever `weights`; Weaviate `alpha` interpolates dense/sparse; OpenSearch `normalization-processor` has per-retriever `weights: [0.3, 0.7]` (its docs: reach for it "when you have judged data showing one retriever should dominate"). The Neural Base hybrid course: `RRF_final = w1·Σ1/(k+r_bm25) + w2·Σ1/(k+r_vec)`, tuned per corpus ("for semantic-heavy domains, increase w2").
- **Vendors' default posture**: Elasticsearch/OpenSearch/MongoDB ship equal-weight RRF as the zero-tuning default; learned weights are "where teams graduate later" once they have behavioral data (Redis) — Tortoise's LongMemEval surface *is* that labeled data.

**Confidence:** high (`3+ source categories`: in-repo paired deltas + Cormack + vendor docs). Highest-priority lever.

---

## Lever 4 — TF-IDF hard-tier lexical+semantic hybrid

### Current mechanism
TF-IDF exists only as the **last-resort fallback** when *all three* FalkorDB strategies fail (`sdk.tortoise_fts_query` → `fallback_tfidf` over `self.query()` payload or the #1375 `fallback_snapshot` cache). It never contributes to the pool when FTS/vector/structural succeed. Its indexed text is `content ∪ search_keys` (`index_text`), the sparse-leg parity surface.

### Hypothesis
The FTS leg (RediSearch scoring over `build_or_query`) and the TF-IDF cosine leg (over the snapshot) are *different lexical signals*: OR-union token matching vs cosine over TF-IDF vectors. Probe C showed the TF-IDF path surfacing a paraphrased evidence point at rank 1 where FTS zeroed it (pre-R2 strict-AND; R2's OR-union closes part of the gap but not the synonym-substitution gap — "go-to" vs "favorite" share no tokens, so OR-union cannot help). Making TF-IDF a **regular 4th RRF leg** (via the cached snapshot, not the 350ms re-fit) keeps lexical signal in the pool even when the embedder is healthy — the mem0/graphiti pattern of "dense-first + always-on sparse complement". It also directly hardens the *degraded-embedder path* (the issue's "degraded path uses TF-IDF" framing) by making the hybrid robust before degradation is reached.

### Expected cost/benefit
- **Cost:** snapshot search = single in-memory cosine pass over the lean corpus (~ms for the eval's per-question graphs; the snapshot build is cached per graph/key #1375). Threading a 4th leg into `degradation_chain` + `rrf_fusion` (or, cheaper, folding TF-IDF hits into the FTS leg's list pre-fusion) = small. **Caution:** the snapshot must stay digest-keyed / invalidated on write (existing `snapshot_key` machinery) or the leg serves stale points.
- **Benefit (expected):** recall lift for paraphrase-heavy queries; robustness. The probe C evidence is the strongest in-repo signal. External: mem0 fuses `semantic + BM25 + entity` additively and reports keyword as the *primary* signal for factual/exact queries ("What meetings did I attend last week?") — the dense leg alone is not the answer for entity/exact lookups; graphiti runs BM25 + vector + BFS per scope. All three competitors are "dense-first, sparse always-on".
- **Pitfall:** adding a 4th equal-weight leg further dilutes the vector leg — **this lever interacts with fusion-fix (L3)**. The plan must run them as a joint sweep (TF-IDF on/off × weight grid), not independently, or the dilution from L4 could mask the L3 gain.

### External precedent
- **mem0 v3**: normalized BM25 (`midpoint/steepness` sigmoid normalization), lemmatized, over-fetch `max(limit*4, 60)`, additive score `(semantic + bm25 + entity)/max_possible`. BM25 = primary signal for factual/exact queries.
- **Zep/graphiti**: BM25 fulltext (Lucene, OR-tolerant "OR + sanitize" per the v2 report's competitor scan) + vector + BFS per scope, RRF-fused; episodes (raw chunks) are a first-class scope, not a fallback.
- **Letta**: pure dense + metadata/tag/date filters — the counter-example validating that a strong dense leg can stand alone; the v2 report's verdict was "the dense leg is the default backbone everyone else builds on", with sparse as complement, not replacement.
- **The v2 report's own recommendation #3**: "Prefer BM25 over strict-AND FTS for the sparse leg (mem0 pattern) or blend: FTS pool ∪ TF-IDF pool before RRF; never let a single strategy's AND semantics zero out the answer."

**Confidence:** medium-high (`2 source categories` + in-repo probe C — the mechanism is proven locally; the always-on-leg choice (4th leg vs FTS-blend) is an implementation decision).

---

## Cross-lever synthesis

| Lever | In-repo evidence | External precedent | Prod cost (300ms) | Expected Δ (turn_recall@10 / nDCG@10) | Priority |
|---|---|---|---|---|---|
| **L3 fusion-fix** | fused < vector by −1.95 nDCG pts (embedded); hybrid > vector on HNSW (under-weighted, not broken) | Cormack k=60 not critical; weighted RRF in Mongo/Weaviate/OpenSearch; "weights justified when judged data shows a dominant retriever" | ~0ms | nDCG +2–4 pts (ordering), recall flat-ish | **1** |
| **L4 TF-IDF hard-tier hybrid** | probe C: TF-IDF rank-1 where FTS zeroed; snapshot infra exists | mem0 BM25 always-on; graphiti BM25 leg; v2 report rec #3 | ~1–5ms (cached) | recall +2–5 pts on paraphrase-heavy cats; robustness | **2** |
| **L1 key-expansion (search_keys PRF)** | E3 #1535 search_keys hook unused; probe C vocab mismatch | mem0 entity expansion (no LLM); HyDE helps sparse +26.7% vs dense +4.0%; ACL-2025 leakage caveat | ~0–5ms (rule-based; HyDE = eval-only) | recall +1–3 pts IE-ish; nDCG flat | **3** |
| **L2 time-aware QE (query-side)** | R5 already shipped retrieval-side temporal; KU category has no recency handling | mem0 temporal intent (no LLM call, additive); AutoMem bias=auto; temporal-rag adaptive weight; "invert for explicit-date queries" | ~0ms | +1–2 pts on KU/TR (bounded by R5) | **4** (extend-TR-intent-to-KU is the real delta) |

**Sequencing vs #317:** all four levers are budget-compatible and orthogonal to #317's CE reranker. #317 remains gated on GPU/API-served inference. The R6 rerank stage (already default-off in the eval) stays the measurement surface for when serving exists. Recommended order: **L3 → L4 → L1 → L2** (evidence-to-cost descending), with L3×L4 run jointly to control the dilution interaction.

**Source confidence summary:**
- High: L3 (in-repo paired deltas + Cormack + vendor docs — 3 categories)
- Medium-high: L4 (probe C + mem0/graphiti/v2-report — 2 categories)
- Medium: L1 (probe C + SemEval/EACL/ACL — external deltas benchmark-inflated, leakage caveat) ⚠️ emerging
- Medium-low: L2 query-side (single-source novel variant; R5 retrieval-side is shipped/measured) ⚠️ single-source
