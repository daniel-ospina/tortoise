---
title: "Research brief — #1349 embedder swap (evidence-gated selection)"
type: research
domain: capability
doc_status: live
created: 2026-08-17
issue: 1349
ownedBy: epistemic-team
---

# Research Brief — #1349: Embedder Selection for Hosted Tortoise

> Phase 1.5 axis matrix (issue-scoping v5.1). Findings date: 2026-08-17.
> Source tags: canonical / competitor-precedent / pitfalls.

## Axis 1 — Model selection (Library-deps, triggered)

- **No official Meta embedding model exists.** "New Meta-architecture" embedders are NVIDIA Llama-3.2 fine-tunes (llama-nemotron-embed-1b-v2, llama-embed-nemotron-8b): 1B/8B, 2048-dim MRL, GPU/API-tier only (A100-benchmarked), NOT Docker-CPU candidates. Sources: HF nvidia/llama-nemotron-embed-vl-1b-v2, arXiv 2511.07025.
- **2026 small-CPU best-in-class (ranked):**
  1. Qwen3-Embedding-0.6B — native 1024-dim (MRL-reduced 768), 32K ctx, LongMemEval R@10 78.71% (single-source mem0 figure ⚠️) — mem0's production default for memory retrieval
  2. nomic-embed-text-v1.5 — MTEB ~62, 768-dim, MRL 64–768, 8192 ctx
  3. snowflake-arctic-embed-xs/s — 22M/33M, 384-dim (xs is fine-tuned FROM all-MiniLM-L6-v2; +8.2 MTEB-R at identical size)
  4. bge-small-en-v1.5 — 384-dim, 33M, minimal-footprint classic
  Sources: mem0 GitHub/docs, premai 2026 ranking, promptquorum 2026.
- **Qwen3-Embedding-0.6B is the notable omission** from the issue's candidate list — current small-CPU accuracy leader, exact model mem0 benchmarks memory retrieval on.
- **Pitfalls:**
  - Instruction-prefix asymmetry (BGE query prefix, e5 query:/passage:, nomic task-instruction, nemotron Instruct:..Query:) — Tortoise uses ONE model for symmetric cross-lens AND asymmetric search, so pick a prefix-tolerant model or apply the same prefix universally.
  - BEIR contamination (BGE trained on MS MARCO — treat published BEIR/MTEB as upper bounds, not the decision metric).
  - normalize_embeddings=True required for FalkorDB cosine.
  - Dimension is fixed per index — changing dim = full re-embed + reindex event, not a config flag.

## Axis 2 — Architecture (medium)

- **FalkorDB vector index:** `CREATE VECTOR INDEX ... OPTIONS {dimension, similarityFunction:'cosine'|'euclidean', M, efConstruction, efRuntime}`; dims 1–4096; DROP VECTOR INDEX + recreate for rebuild; dimension change NOT in-place — full re-embed + recreate required; index is per (label, attribute) — tortoise has per-label indexes (Point/Event/Document/Source/Object/Subject), so a swap = per-label drop+recreate + full re-embed. Sources: docs.falkordb.com vector-index, mem0 blog.
- **Threshold calibration:** no universal cosine cutoff — sweep against a labeled near-dup/paraphrase/related/unrelated pair set per model; bands overlap across models (true paraphrases as low as 0.564, unrelated as high as 0.755); thresholds NOT portable — re-score tortoise's own labeled set under the new embedder. Sources: mixpeek calibration guide, clawrxiv 2604.01081, arXiv 2601.16907.

## Axis 3 — Hosted-vs-local embedding UX (user CRITICAL)

- **mem0 hosted tier: SERVER-SIDE baked model.** "We serve the model ourselves" — every fact + entity name embedded server-side on each add; queries embedded on each search; customers authenticate via API key; no customer-side model runtime. OSS tier defaults OpenAI text-embedding-3-small, overridable via env — still server-side in the customer's deployment. Sources: mem0 engineering blog Jul 2026, docs.mem0.ai, mem0 GitHub api-mapping.md.
- **Zep:** defaults OpenAI Ada-002; removed its bundled local embedding service in CE (trend away from customer-local); supports BYO-vectors manually. **LangMem:** server-side OpenAI default. **Pattern:** every major hosted memory product (mem0, Zep, LangMem, Letta) = embedding runs inside the service; customer control = model choice via config, NOT customer-local compute. Sources: Zep embeddings docs, LangMem quickstart.
- **Customer-local over API/MCP pitfalls:** payload (384-dim float32 ≈ 1.5KB/point across the boundary), model download (MiniLM 90MB → Qwen3-0.6B 1.2GB+), torch runtime ~2GB+ on customer side, HF download blocking on corp networks, version skew → mixed vector spaces in the same graph (index-consistency hazard), per-query latency, loses server-side caching/parallelism. E2E-8 ≤300ms p95 only controllable server-side.

## Synthesis

For hosted Tortoise the precedent is unambiguous: **embeddings should be server-side (baked model)** — mem0/Zep/LangMem all run embedding inside the service stack; customer-local embedding is the self-hosted story. Baked model class = small encoder; benchmark candidates: all-MiniLM-L6-v2 (control), bge-small-en-v1.5, arctic-embed-xs, arctic-embed-s, nomic-embed-text-v1.5, **+ Qwen3-Embedding-0.6B**. NVIDIA Llama-3.2 fine-tunes are the GPU/API-tier upgrade path later. Swap gated on re-scored evidence (BEIR-contaminated, cosine bands non-portable), not published numbers.

## Raw Notes

- MemDelta (arXiv:2606.29914, 2026): pure embedding swap on identical pipeline = +6.2pp LongMemEval-S (p=0.004), largest on temporal (+10.5pp) and multi-session (+11.3pp). Single-source preprint — treat with ⚠️ caveat.
- LongMemEval paper (arXiv:2410.10813): 4 control points (value/key/query/reading); embedder not among them. But MemDelta shows embedding swap is a real bounded lever.
- AutoMem (FalkorDB graph+vector, LongMemEval #48): 97% R@5; 54/65 remaining errors had gold in top-5 → synthesis, not retrieval. Beyond ~95% R@5 bottleneck is reader.
- LongMemEval leader Hindsight (91.4%): commodity embeddings ("any OpenAI-compatible API + LiteLLM") — embedder is a swappable config, not the differentiator; bound = recall coverage, not top-10 ordering.
- In-repo #1144 baseline-embedded-2026-08-17.json: vector arm 0.8546 nDCG@10 with SYNTHETIC topic-centroid stand-in vectors (model-independent, near-ceiling); hard tier TF-IDF 0.599 > vector 0.530; fused 0.835 < vector 0.855. No real-model Docker baseline committed.
- snowflake-arctic-embed-xs (22M/384): MTEB-R 50.15 vs MiniLM 41.95 (+8.2 at identical size — gain is training data, not parameters); arctic-embed-s (33M/384): 51.98 > bge-small 51.68.
- bge-small-en-v1.5: 33.4M/384/~127MB/MIT, v1.5 designed to work WITHOUT the retrieval instruction prefix (query-side prefix optional, small quality drop if omitted — benchmark protocol commits to no prefix for single-model symmetric+asymmetric parity), ~1.7-2x slower per encode than MiniLM on CPU.
- Qwen3-Embedding-0.6B (upgrade path, NOT a benchmark candidate): 0.6B params, native 1024-dim (MRL-reduced 768), ~1.2GB, instruction-aware — LongMemEval R@10 78.71% (single-source mem0 figure, ⚠️). Excluded from this issue's pool: 1024/768-dim collides with the #265 encrypted tier's 384-dim pinned client embeddings + 2GB VM feasibility.
- 384-dim constraint is NOT a false economy for THIS issue: driven by the #265 encrypted-tier shared-index pin (client 384-dim MiniLM vectors; server never recomputes), not reindex cost. Dimension upgrade (nomic 768 / Qwen3 1024) requires cross-epic #265 coordination + VM feasibility — filed out as a separate issue.
- BEIR averages hide variance (Kamalloo SIGIR 2024); BGE trained on MS MARCO (BEIR in-domain); contamination concerns.
