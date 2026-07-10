# E017 Align Decision

## Strategy Alignment Decision

**Experiment:** E017 — Sequential document processing + graph construction
**Decision:** PROCEED
**Level:** experiment (project depth)

## Alternatives Considered

1. **Continue iterating on E016's clean-proposition design** — Rejected: E016 proved clean propositions create no extraction load. The graph's value is in extraction, not organization of already-extracted claims. More design iterations on the same paradigm won't yield different results.

2. **Test graph as retrieval tool (RAG benchmark)** — Rejected: different research question. Retrieval benchmarks test "can you find the relevant fact?" — this experiment tests "can you maintain coherent beliefs across sequential noisy documents?" The graph as a belief-tracking tool, not a search tool.

3. **Reference-graph → noisy-documents → agent pipeline (CHOSEN)** — Tests the graph's value end-to-end: extraction from noise, cross-document connection-making, resistance to recency bias. Produces quantitative comparison between arms. Directly informs Tortoise design decisions.

## Experiment Value

**Knowledge gain:** Answers whether the graph measurably improves agent belief coherence when information arrives sequentially and noisily — the actual human-like scenario. E013-E016 tested isolated mechanisms; this tests the integrated pipeline.

**Design impact:** If Tortoise produces stable, reference-graph-aligned answers regardless of document order while Control varies → validates the graph's core value proposition. If both arms vary similarly → the graph needs redesign.

**Cost:** Low. ~50 LLM calls for reference graph construction + ~20 for validation run + ~200 for scale. Total ~$2-5 in API costs.

## Key Assumptions

- A 40-page document set from a single reference graph provides enough noise to create recency bias in Control — **confidence: medium** (untested; E016 showed 700 tokens was too little)
- Cross-document connections (points that only make sense when you've seen multiple documents) will be discoverable by graph but missed by Control — **confidence: high** (this is the graph's core mechanism)
- Graph similarity to reference graph is a measurable and discriminative metric — **confidence: medium** (need to define similarity metric)

## Recommendation

PROCEED. This experiment tests the graph's value in a realistic scenario (sequential noisy documents, cross-document connections) that none of E013-E016 addressed. The reference-graph-first design ensures we know the ground truth before adding noise — the opposite of E016's approach, fixing its root failure.
