---
title: "Retrieval layer — v2 extractor LongMemEval full-run (2026-08-19)"
type: log
domain: capability
doc_status: draft
created: 2026-08-20
subjects.team: epistemic-team
---

# Retrieval layer — v2 extractor LongMemEval full-run (2026-08-19)

**Verdict: the v2 full run is NOT a valid v2 measurement — the extractor wrote zero points in 444/496 questions (89.5%); the direct-DeepSeek key returned HTTP 402 Payment Required on 21,329 S1 calls (+1,081 S2 / 903 S4 timeouts, 21,957 "no embed list" errors). The graph retrieval measured contained RAW TRANSCRIPTS ONLY.**

## Retrieval findings
- **evidence_recall@5 = 0.0 is vacuous, not a retrieval failure.** 495/496 questions had ZERO has_answer=true points (the extractor never wrote them). The metric can't distinguish "evidence exists but never surfaces" from "no evidence exists" — a measurement-design bug in #1369. A prior 4-question v2 run with working extraction showed evidence_recall@5 = 0.25 — evidence points DO surface when they exist.
- **The session-recall drop (80.6→75.4) measures raw-transcript-only retrieval**, not v2 graph retrieval. Without turn points, every session's transcript is a giant bag containing the question's tokens → top-5 becomes a lottery. Context tokens exploded 4.4× (7.9k→34.9k).
- **Genuine retrieval risk that survives the 402 failure (empirical probe):** FTS in this build is **RediSearch strict-AND-over-tokens** — a paraphrased evidence point is zeroed by a one-token mismatch, while the verbatim raw transcript always wins. Even the baseline's verbatim evidence turn failed the full-question FTS query (stopword/possessive quirks). The two legs are non-uniform per graph (small → TF-IDF fallback; larger → FTS; match_source never recorded).

## Where we do well / not
- **Well:** raw-transcript verbatim recall survives total extraction failure (0.75 sr@5 with 0 points — the #1369 mitigation works); graceful degradation + checkpointing; idempotent writes; the attribution machinery exists.
- **Not well:** no run-integrity gate (21k errors published as a result); paraphrased points vs lexical AND-match; **the vector leg is absent in the eval** (no embedder); the structural leg is inert (run_structural_query with kind=None returns []); raw-transcript domination with no diversity control; metric blindness (never-written vs never-retrieved indistinguishable).

## Competitor stack (all converge on the same architecture)
- Graphiti/mem0/letta all use: **BM25 sparse + dense embeddings + entity linking + rerank (cross-encoder) + diversity (MMR) + temporal scoring**.

## Recommendations (ranked)
1. **P0 — gate v2 runs on extraction health:** flag/abort when evidence_points==0 or error rate >20%; persist match_source + evidence count + points written per question; re-run with a funded key.
2. **P0 — record the leg mix per question** (fts/vector/structural/tfidf + pool size) so numbers are attributable.
3. **P1 — BM25/OR-tolerant sparse leg** instead of strict-AND FTS (mem0 pattern); blend FTS pool ∪ TF-IDF pool before RRF.
4. **P1 — enable the dense leg in the eval** (local MiniLM embedder) — every competitor is dense-first; it's the only leg that rescues paraphrase. Biggest single lever for v2 evidence recall.
5. **P1 — chunk raw transcripts to turn-granularity** + session-dedup/MMR in top-k (cuts the 4.4× context, restores turn-level attribution).
6. **P1 — wire the structural leg:** 1-hop IMPL/NAND expansion from matched points (Graphiti's episode-mentions pattern) so the graph amplifies recall.
7. **P2 — cross-encoder/LLM rerank + MMR diversity + temporal scoring.**

**Bottom line:** void the v2 full run as a v2 measurement (it's a raw-transcript control). Re-run with the extractor key fixed (pre-flight API ping + fail-fast on 4xx). Keep BOTH legs (raw transcripts = the working verbatim recall; extracted points = the epistemic layer) but make them non-redundant: turn-granular chunks + evidence-marked points that are actually retrievable (densify or append verbatim answer tokens).
