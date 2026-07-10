# E017 Research Synthesis

**Date:** 2026-07-10

## What We Have Internally

- **E013-E016:** Six designs tried. E013 tested extraction (too noisy), E014-E016 tested known operators (100% both arms — clean propositions need no organizing).
- **Root cause:** We gave agents pre-extracted, one-liner claims. The graph's value is in extraction from noise, not organizing already-clean propositions.
- **E016 key insight:** The experiment should start from raw text where extraction load is real and cross-document connections must be discovered, not presented.

## External Findings

### Recency Bias in LLMs ⚠️ medium (2 sources)

LLMs exhibit **extreme recency bias** in sequential tasks. They match a theoretical "recency player" (ignores all history except the last trial) in **78-91% of decisions** — far higher than humans. When sequentially processing information, LLMs' decisions are dominated by the most recent inputs, with "almost complete neglect" of information from earlier in the sequence.

**Implication for E017:** The Control arm (sequential raw-text ingestion, no external memory) should show strong order effects — the agent's final answer will depend heavily on which documents appeared last. The Tortoise arm (building a graph incrementally) should stabilize against this because the graph preserves all evidence equally.

### Graph-As-Memory Experimental Design ⚠️ single-source

Standard pattern for KG memory experiments:
1. Both arms process the same documents
2. Control: within-context-window only (must summarize or lose state)
3. Treatment: external memory tool (read-write, persist + retrieve)
4. Metrics: cross-document accuracy, entity consistency, token efficiency
5. Ground truth: pre-built reference graph for comparison

### Graph Similarity Metrics ⚠️ medium (2 sources)

For comparing agent graphs to reference graphs:
- **Node overlap:** Jaccard on entity sets — simple, interpretable
- **Edge overlap:** Jaccard on relationship pairs — captures structure
- **Embedding similarity:** EmbPairSim — top-performing but complex
- **Practical approach:** Node overlap (recall of reference graph entities) + edge overlap (recall of reference graph relationships) + thesis alignment (does the final answer match?)

## Recommendation

**Proceed with E017 design:**

1. **Reference graph first** — build a complex business case graph (Series A startup, should-we-pivot) with 30-50+ points organized into trees, loops, and linear argument chains
2. **Document generation** — expand the graph into ~40 pages of noisy reports where evidence is buried in context and cross-document connections require multi-hop reasoning
3. **Sequential ingestion** — feed documents in 4-5 batches, asking agent "what do you think?" after each (Control) or "file this into the graph" (Tortoise)
4. **Metrics:**
   - Graph similarity: node overlap + edge overlap against reference graph
   - Answer stability: variance in final answer across document orderings
   - Per-batch recency effect: how much each batch's content dominates the answer

The recency bias literature strongly predicts the Control arm will be order-dependent while the Tortoise arm remains stable.
