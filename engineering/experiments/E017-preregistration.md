# E017 Pre-Registration

**Date:** 2026-07-10
**Status:** ⛔ PRE-REGISTERED (no data collected)
**Pipeline:** experiment-workflow → Stage 3

## Research Question

Does an agent using Tortoise (incremental graph construction + structured retrieval) produce more stable and reference-graph-aligned conclusions than an agent processing the same documents sequentially from memory, when evidence is distributed across noisy documents and presentation order varies?

## Background

E013-E016 tested the graph in isolation with pre-extracted propositions. All designs that gave both arms equal information (v1-v3, v5-v6) showed zero discrimination — DeepSeek integrates clean propositions perfectly. Only v4 discriminated, but it tested memory prosthesis (Control had no history), not graph-as-organization-tool.

LLM recency bias literature shows agents match a "recency player" in 78-91% of sequential decisions — the most recent inputs dominate conclusions. An external memory tool that persists all evidence equally should resist this bias.

## Hypotheses

### H1: Graph Stability (Primary)
> Agents using Tortoise produce more stable conclusions across document orderings than agents using raw sequential memory.
- **Metric:** Variance in final answer confidence across 4 order variants. Lower variance = more stable.
- **Expected:** Tortoise σ²_confidence < Control σ²_confidence (directional)

### H2: Graph Alignment (Primary)
> Tortoise-constructed graphs are more similar to the reference graph than Control's memory-only conclusions.
- **Metric:** Node overlap (Jaccard) between agent's extracted claims and reference graph claims. Edge overlap between agent-identified relationships and reference graph operators.
- **Expected:** Tortoise Jaccard > Control Jaccard (directional)

### H3: Cross-Document Connection Discovery (Secondary)
> Tortoise discovers more cross-document relationships (points that require information from 2+ documents) than Control.
- **Metric:** Count of correctly identified cross-document operators vs reference graph cross-document operators.
- **Expected:** Tortoise cross-doc recall > Control cross-doc recall

## Falsification Criteria

**The experiment is falsified if:**
1. Both arms show equal stability across order variants (σ²_confidence within 10% of each other, AND node overlap within 5 percentage points)
2. Neither arm discovers any cross-document connections (both arms at 0%)

**A null result means:** The graph does not add measurable value over raw sequential processing for this task complexity. The graph's value proposition requires either larger data volumes or extraction-noise environments.

## Experimental Design

### Reference Graph
- Complex business case: Series A startup, 1 year post-funding, "should we pivot?" decision
- 30-50+ points across domains: product analytics, user research, competitor landscape, financials, team dynamics, market trends, investor relationships
- 8-15 operators: NANDs, supports, correlates — forming trees, loops, and linear chains
- Known ground truth answer (from graph structure)

### Document Generation
- 8-12 documents (reports, memos, dashboards) totaling ~40 pages
- Each point is embedded in context with padding/noise
- Cross-document connections are NOT stated in any single document — require multi-hop reasoning
- No document states the answer directly

### Experimental Conditions

**Both arms receive the SAME documents in the SAME batches.**

**Control Arm:**
```
Batch 1: "Here are documents 1-3. What's your assessment?"
Batch 2: "Here are documents 4-6. Has your assessment changed?"
Batch 3: "Here are documents 7-9. What do you think now?"
Batch 4: "Here are documents 10-12. Final assessment — should we pivot?"
```
No external memory. Agent can reference prior responses but must hold all context in the prompt.

**Tortoise Arm:**
```
Batch 1: "Here are documents 1-3. File the key claims into the graph."
Batch 2: "Here are documents 4-6. File the key claims and any tensions you see into the graph."
Batch 3: "Here are documents 7-9. Continue filing into the graph."
Batch 4: "Here are documents 10-12. File into the graph. Then, using the complete graph, give your final assessment — should we pivot?"
```
Agent constructs a graph incrementally. Final answer uses the graph.

### Order Variants
4 document orderings to test stability:
1. **Chronological** — documents in narrative order
2. **Reverse** — last document first
3. **Domain-clustered** — product docs, then financial docs, then team docs
4. **Interleaved** — alternating between positive/negative signals

### Metrics

| Metric | Measure | Baseline (Control) | Target (Tortoise) |
|--------|---------|-------------------|-------------------|
| Node overlap | Jaccard(agent claims, reference claims) | 0.3-0.5 | >0.6 |
| Edge overlap | Jaccard(agent operators, reference operators) | 0.1-0.2 | >0.4 |
| Answer stability | σ(confidence) across 4 orders | >15% | <10% |
| Cross-doc recall | Correct cross-doc ops / total | 0-20% | >40% |
| Final answer match | Does answer match reference? | 50-75% | >75% |

### Model
- deepseek/deepseek-chat via OpenRouter
- T=0.3 (non-zero for scale variation)

### Sample Size
- Validation: 1 run, 4 order variants, 2 arms = 8 trials
- Scale: 10 runs × 4 variants × 2 arms = 80 trials

## Analysis Plan

1. **H1 (Stability):** Compare σ² of final confidence scores between arms. Lower σ² = more stable.
2. **H2 (Alignment):** Compare Jaccard node/edge overlap between arms using Welch's t-test (or bootstrapped CI if non-normal).
3. **H3 (Cross-doc):** Compare count of correct cross-document operators. Fisher's exact test on per-variant rates.

## Limitations (Pre-Registered)

1. **Single reference graph** — results may not generalize to other business scenarios
2. **T=0.3** — introduces stochastic variation but also noise; deterministic T=0 avoids this but eliminates scale variation
3. **Graph similarity metrics** — Jaccard treats all nodes/edges equally; some may be more important than others
4. **DeepSeek only** — results may not generalize to other models with different recency bias profiles
5. **No extraction noise** — documents are human-authored from a known graph, not organic company reports. The extraction step has a known ground truth, which may overstate the graph's value compared to real-world extraction from truly noisy sources.
