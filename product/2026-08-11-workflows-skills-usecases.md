---
title: Tortoise — core workflows + skills to optimize tooling for
type: product-usecases
domain: product
status: live
created: 2026-08-11
updated: 2026-08-11
---

# Tortoise — core workflows + skills

> Purpose: the tooling (SDK / MCP tools / agent skills) must be optimized around these
> workflows. SDK/MCP/tools come FIRST, then the skills that orchestrate them. Preserve
> light / medium / high thoroughness variations throughout.

## 1. Recall tool design (epic #898)

**One tool, 3 preset modes + 1 custom mode** (preset + override pattern — good defaults without
relying on skills, but every param stays individually overridable):

```
tortoise_recall(query|seed, mode, ...overrides)
  mode="state"     → UC1 defaults: confidence-gated current state
  mode="gaps"      → UC2 defaults: under-supported claims
  mode="subgraph"  → UC3 defaults: complete topic subgraph
  mode="custom"    → raw params, full control
```

Each mode sets tuned defaults for the underlying params (`confidence_gate`, `rank_blend`,
`min_confidence`, `include_superseded`, `focus`, `depth`, `completeness`, `min_relevance`,
`recency`, `surface`). Any param can be overridden per-call.

### The three intents
- **UC1 — STATE (primary):** what is true & high-confidence right now. Not superseded, not
  deprecated; mostly objects + the most important arguments + important NANDs/mitigations.
  Ranking: multiplicative confidence gate (option b chosen) —
  `relevance × confidence^b × (1 + w_c·centrality)`. Low support ranks lower; contradicted
  claims are FLAGGED & shown (with counter-evidence), not buried.
- **UC2 — GAPS:** what's not properly supported — claims that provide confidence (IMPL) or a
  strong NAND but aren't themselves sourced/supported by other claims. Feeds the reasoning
  cycle (investigate weak spots → research more). Graph-structure query, not semantic.
- **UC3 — BROAD SUBGRAPH:** whole subgraph for a topic, completeness-optimized. Used before
  connecting a new document to the graph (deep understanding first).

Existing machinery: `ranking.py` GraphRanker already fuses relevance + confidence + centrality
(degree) + recency, and already implements "contested = surfaced, not penalized." Gaps: not the
default path; no state semantics; confidence under-weighted for UC1; UC2/UC3 absent.

## 2. Core workflows to optimize tooling for

| Workflow | What it does | Notes |
|---|---|---|
| **recall** | UC1 above. | Primary read path. |
| **index** | Add metadata to files (agentSessions, meeting summaries, docs, etc.), then index the metadata as **sources/events** that can be searched. | Files → metadata → Source/Event nodes → searchable. |
| **mine** | Extract entities (points, objects, subjects, events) from sources. Understand the logical structure WITHIN a source to map what's important in it. | Two directions, both used: **inside-out** (source-internal relevance → graph) and **outside-in / serendipity** (broader graph context → what in the source is relevant to the graph → add). Serendipity amount is a **user-settable param** (more serendipity = more cost). |
| **connect** | Connect new data: **IMPL/NAND other objects** if it affects their validity; **IMPL/NAND operators** if it mitigates (+/−) the relevance of existing connections. Then **run EP in the subgraph**. | Distinguishes affecting truth (objects) vs affecting relevance (operators/edges). |
| **ingest** | Data added via **mine** OR via an **agent filling in data**. Fill-in granularity option: very granular (one entity/edge/operator at a time) or more in-bulk. | Granularity is a param. |
| **run EP in subgraph** | Local belief propagation over an affected subgraph. | Fast, targeted. |
| **dreaming** | Run EP across the whole graph (or at least expanding), keeping the whole graph fresh. | Global / expanding refresh. |

## 3. Skills that orchestrate the workflows

SDK/tools/MCP first, but preserve the skills — both the light "how to" and the complex ones.
Keep **light / medium / high** thoroughness variations.

### how-to-use-tortoise (light)
Existing skill. Preserve light/medium/high variations for thoroughness.

### tortoise-decide (the core "show what this can do that's valuable")
1. Refine decision definition with the user.
2. Research options and criteria.
3. Check with user: list of criteria (for value) + options (for completeness).
4. Connect criteria → option nodes.
5. Research IMPL/NAND **mitigations** to the option-criteria connections. (Sometimes you find
   things NANDing an option itself — e.g. "out of stock" — but mostly they're mitigations,
   because they affect the option's **relevance**, not its **truth**.)
6. Optional: research mitigations to the mitigations (each mitigation can carry an operator on
   the edge → enables sub-mitigations).
7. Options can also IMPL/NAND among themselves (e.g. two go well together; three others are
   incompatible / mutually exclusive with those two).

### research-domain (part of tortoise-decide)
Research new data (references the modular research skill: Perplexity calls, scientific-paper
search, etc.) to go broad on a subject → connect data to itself + pre-existing data (mine) →
analyze what needs more research (graph **UC2**: find weak spots) → launch research again → mine
the new research → **repeat UC2 + research + mine**. By the end of cycle 3, provide a thorough
analysis.

### analyse-for-contradictions
Analyze a plan / issue / design / strategy against the graph to spot contradictions with
previous decisions and with high-confidence points.

## 4. Priorities
1. **SDK / MCP tools / agent-tools consolidation + recall epic (#898)** — FIRST.
2. Workflow tooling (index, mine, connect, ingest, EP-subgraph, dreaming).
3. Skills: how-to (light/medium/high), tortoise-decide, research-domain, analyse-for-contradictions.
