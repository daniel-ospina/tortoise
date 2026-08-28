---
title: "Tortoise Blog Keyword Research — Topic Taxonomy + Keyword Map (port of ElDato KEYWORD_RESEARCH_MASTER methodology)"
type: synthesis
domain: growth
doc_status: live
created: 2026-08-28
ownedBy: organisation-design-team
aboutObjects: tortoise-website
findings-date: 2026-08-28
issue: 1862
---

# Tortoise Blog Keyword Research — Master Reference

**Findings date:** 2026-08-28
**Keywords analyzed:** 164 (136 taxonomy-tiered in §4.1–4.12 + 28 value-prop layer in §4.13)
**Actionable in module:** 147 — `website/functions/blog/_shared/seo-keywords.ts` carries the 147 injection-ready keywords; the 17 excluded are 4 skip terms (extreme competition), 3 T3-watch head terms (agent memory / semantic memory / episodic memory — generic heads better as content topics than meta injection), 1 near-duplicate (agent memory vs rag ⊂ rag vs agent memory), 5 Domain-confidence brand-line phrases (content vocabulary, not search targets), and 4 value-prop content-only terms (behavioral state decay, runtime continuous learning, agents that need less supervision, memory instead of model scale — research/Domain vocabulary that shapes content, not meta).
**Data sources:** GSC (tortoise.premiselabs.co — empty, new domain) · SERP analysis (live, 2026-08-28) · domain taxonomy inventory (docs/ONTOLOGY.md, website surfaces, blog epic scope)
**Methodology port:** ElDato `KEYWORD_RESEARCH_MASTER.md` (2026-07, 1,414 keywords analyzed) — **methodology only, keywords are Tortoise-specific**

> **Volume/difficulty caveat (honest sourcing):** Google Ads Keyword Planner access was not configured during this research pass (C1 clarification pending at scoping; default was Planner + SERP). Volumes below are **SERP-derived estimates** (banded ranges) calibrated from live 2026-08-28 SERP analysis of each cluster — the competitive surface (who ranks, how many players, content format) is the difficulty signal. **Refresh path:** GSC query export becomes the primary input once the blog accrues data (≥6 months); Keyword Planner/DataForSEO can replace estimates at v2. Every keyword below is tagged with a source-confidence mark: `SERP-est` (SERP-derived estimate) or `Domain` (product-domain term, no reliable volume data yet — Strategic tier default).

---

## Executive Summary

1. **The blog is a category-creation play, not a demand-capture play.** Tortoise's core differentiators — *epistemic* memory (belief graphs, claims + confidence, NAND contradiction, EP belief propagation) — are **zero-competition category terms** that no incumbent memory system (Mem0, Letta/Zep/Graphiti, Cognee, Hindsight) targets. Every competitor positions on *what* agents remember; none positions on *why* the agent believes it. This is the Strategic tier's anchor.
2. **Highest-volume category: "agent memory"** (the umbrella term). SERP shows a hot 2026 landscape: Mem0 (~63k★), Graphiti/Zep (~30k★), Letta (~24k★), Cognee (~30k★), Hindsight (~19.6k★), plus a dense blog/guide layer (IBM, Redis, Elastic, fountaincity.tech, digitalapplied, devtoollab). Tortoise must enter this space with **differentiated angle** (epistemic/belief/confidence + provenance), not head-on "agent memory" (competition: extreme).
3. **"Graph vs vector" is the live argument** — and it's unresolved. Multiple 2026 pieces argue graphs are overkill (Mem0 dropped its graph module v3, April 2026; a Medium postmortem benchmarked graph memory at ~$14 vs $0.03 for embeddings and still lost recall). Others argue graphs win on multi-hop/provenance/temporal. **This debate is Tortoise's wedge**: the epistemic layer is *neither* — claims-with-confidence is a third position that resolves the false dichotomy (see Adversarial Review, A1).
4. **MCP is high-volume but saturated** — modelcontextprotocol.io owns the "what is MCP" head terms. Tortoise's entry point is the *intersection*: "MCP memory server", "knowledge graph memory via MCP", "self-hosted agent memory" — where memory-specific MCP servers (OpenMemory/Mem0 MCP, Graphiti MCP) are new and content is thin.
5. **Tier distribution is heavy on Quick Win + Strategic** — appropriate for a zero-authority domain. No Tier 1 (vol ≥1,500 & diff ≤20) exists in this space: every high-volume term (agent memory, knowledge graph, MCP, RAG) carries extreme competition. The realistic T1-equivalent is *category leadership on low-diff Strategic terms* + Quick Wins on mid-volume low-diff terms.
6. **No ElDato marketplace keywords leaked** (Riviera Maya/PDC/cenotes/cities — verified absent; guard in §9).
7. **Value-prop layer added (2026-08-28, owner direction):** the four candidate value propositions — *memory without drift / stays current*, *memory that learns*, *memory that increases autonomy*, *memory that makes agents smarter* — were tested against live search language and folded into the keyword map as §4.13. Calibration result: **drift and stays-current are one coin and the strongest signal** (dedicated market vocabulary: "context rot", "memory drift", "why ai agents forget", "agent gets dumber over time" — with multiple 2026 blogs/guides ranking on each, including Hindsight, MindStudio, LogRocket, BrainGrid, dev.to); "self-evolving / self-correcting memory" is the hot 2026 promise (SelfMem, MemRL, ReMe, TMEM, WebCoach, Mem2Evolve — the research frontier is entirely here); "agent autonomy" has real volume but reads as an outcome, not a pain; "smarter agents" is commodity (everyone claims it). The module injects all but the **4** research/Domain content-only terms.

---

## 1. Starter Tag Taxonomy (~12 tags)

The blog's taxonomy is free-form `tags text[]` (≤10/post, no migration — `supabase/migrations/20260827000001_blog_cms.sql`). These 12 tags are the **documented starter vocabulary** the keyword map keys on and #1861's generator consumes. Derived from the topic-space inventory (docs/ONTOLOGY.md four-ontology model + website product/docs/self-hosted surfaces + blog epic scope).

| # | Tag | One-line definition | Keyword map |
|---|-----|--------------------|-------------|
| 1 | `agent-memory` | The umbrella category: memory systems for AI agents across sessions — what they are, how to build them, how they fail | `agent-memory` |
| 2 | `epistemic-memory` | Tortoise's core differentiator: memory as *belief* — claims, evidence, confidence, contradiction (why an agent believes X, not just that it remembers X) | `epistemic-memory` |
| 3 | `knowledge-graph` | Graph-structured memory: knowledge graphs, graph databases, GraphRAG, graph vs vector | `knowledge-graph` |
| 4 | `semantic-memory` | Fact/concept memory (Tulving's semantic system) — durable facts, entities, relationships | `semantic-memory` |
| 5 | `episodic-memory` | Event/timeline memory (Tulving's episodic system) — sessions, occurrences, what happened when | `episodic-memory` |
| 6 | `mcp` | Model Context Protocol — servers, memory servers, tool integration | `mcp` |
| 7 | `self-hosting` | Running Tortoise on your own infra — Docker, FalkorDB, privacy/compliance | `self-hosting` |
| 8 | `retrieval` | Getting memory back — hybrid search, RAG, vector search, multi-hop | `retrieval` |
| 9 | `belief-propagation` | The engine: EP (expectation propagation), confidence computation, uncertainty, evidence propagation | `belief-propagation` |
| 10 | `sessions` | Session capture, conversation indexing, mining — how memory gets written | `sessions` |
| 11 | `memory-systems` | Landscape/comparison content — Mem0 vs Graphiti vs Letta vs Cognee vs Tortoise | `memory-systems` |
| 12 | `provenance` | Where memory came from — source attribution, auditability, trust (distinct brand angle) | `provenance` |

Tag count = 12, within the ≤10/post contract on any single post (posts pick ≤10 of these; overlap allowed).

---

## 2. Topic-Space Inventory (where the taxonomy came from)

| Source | Surfaces | Extracted clusters |
|--------|----------|-------------------|
| docs/ONTOLOGY.md §2 (four-ontology model) | Semantic / Epistemic / Episodic / Procedural layers | epistemic (belief) · semantic (facts) · episodic (events) · procedural (state) |
| docs/ONTOLOGY.md §3 | IMPL/NAND operators, EP confidence, CORRECTS/supersession, Source provenance | belief-propagation · provenance · contradiction handling |
| docs/ONTOLOGY.md §4.1-4.6 | Point/Event/Source metadata, status lifecycle | claims · sessions · sources |
| website/index.html | "The primary function of memory is not recall, but learning" / "Memory for Agents to Remember Why, Not Just What" | agent-memory · epistemic-memory (brand position) |
| website/docs.html | "A graph database for agent memory: claims are Points, belief scores computed by propagating evidence" | knowledge-graph · belief-propagation |
| website/self-hosted.html | "Your data, your ops — the same epistemic memory graph, fully self-managed"; Docker, FalkorDB sidecar | self-hosting |
| blog epic scope (01-scope.md) | English-only blog, tags taxonomy, agent publish API | agent-memory (audience = agents/devs) |

**Keyword-eligible cluster set (deduped):** agent memory · epistemic/belief memory · knowledge graph / graph DB · graph vs vector · semantic memory · episodic memory · hybrid search / retrieval · MCP (+ memory servers) · RAG / GraphRAG · self-hosting / local · LLM context / long-context · provenance / trust · belief propagation / EP · session capture / mining · comparison terms (Mem0/Letta/Graphiti/Cognee/Hindsight) · FalkorDB · LLM agents / coding agents.

---

## 3. Keyword Generation Method

Seed terms (from §2) × intent modifiers, deduped near-duplicates:

- Seed terms (23): agent memory, epistemic memory, belief graph, semantic memory, episodic memory, knowledge graph, graph database, graph memory, graph rag, vector database, vector search, hybrid search, mcp server, mcp memory server, llm memory, long-term memory, self-hosted memory, falkordb, belief propagation, expectation propagation, confidence, provenance, session memory, memory for agents, agent memory benchmark.
- Intent modifiers: `what is`, `vs`, `alternative to`, `open source`, `self-hosted`, `tutorial`, `how to build`, `for agents`, `for LLMs`, `database`, `comparison`, `benchmark`, `RAG`, `explained`, `best`.
- Dedupe: exact/near-duplicate phrases collapsed (e.g. "agent memory vs rag" ∪ "rag vs agent memory" → one row, canonical "rag vs agent memory").
- Guard: ElDato marketplace terms (city names, cenotes, beach club, deals, etc.) excluded by construction — the seed list is ontology/product-derived, not tourism-derived (verified §9).

Result: **136 unique keywords** across 12 tag buckets.

---

## 4. Keyword Map (136 keywords, tiered)

### Tier criteria (extended ElDato)

| Tier | Criteria | This data's calibration |
|------|----------|------------------------|
| **Tier 1** | vol ≥1,500 AND diff ≤20 | None qualify in this space (all ≥1,500-vol terms are diff >20) — see note §5 |
| **Tier 2** | vol ≥500 AND diff ≤25 | 2 (+2 borderline T2/T3) |
| **Tier 3** | vol ≥100 AND diff ≤30 | 14 (+4 T3-watch head terms) |
| **Quick Win** | diff ≤15 AND vol ≥100 | 80 |
| **Strategic** | high intent / category-defining, low-or-zero reported vol, low competition (NEW tier — inverts ElDato's zero-volume-is-zero-value) | 30 (taxonomy tiers; §4.13 adds 12 more) |
| Skip | vol <100 AND not Strategic | 4 (extreme competition) |

> Counts are §4.1–4.12 only (136); §4.13 adds 28 (15 QuickWin · 12 Strategic · 1 T3). All 164 rows analyzed; 147 in module; 17 excluded (4 skip + 3 T3-watch + 1 near-dup + 5 Domain brand-line + 4 value-prop content-only).

> **Tier 1 note:** ElDato's T1 bar (1,500+ vol / ≤20 diff) is unreachable for a zero-authority blog in this domain — the vol ≥1,500 terms (agent memory, knowledge graph, MCP, RAG, hybrid search) all carry diff >20 against giants (IBM, Microsoft, Anthropic docs, Mem0, Redis). The strategy substitutes **Strategic-tier category leadership** (low competition, high intent) + **Quick Wins** (diff ≤15). Revisit at v2 when the domain has authority and GSC data.

---

### 4.1 `agent-memory` (umbrella — 18 keywords)

| Keyword | Vol (SERP-est) | Diff (SERP-est) | Tier | Source confidence |
|---|---|---|---|---|
| agent memory | 1,500–2,900 | 35 | T3 (watch) | SERP-est |
| long-term memory for ai agents | 500–1,000 | 22 | T2 | SERP-est |
| what is agent memory | 300–700 | 18 | QuickWin | SERP-est |
| ai agent memory | 300–700 | 20 | T2/T3 | SERP-est |
| memory for llm agents | 200–500 | 18 | QuickWin | SERP-est |
| llm agent memory | 200–500 | 20 | T3 | SERP-est |
| how to build agent memory | 100–300 | 12 | QuickWin | SERP-est |
| agent memory architecture | 100–300 | 15 | QuickWin | SERP-est |
| agent memory vs rag | 100–300 | 10 | QuickWin | SERP-est |
| rag vs agent memory | 100–300 | 10 | QuickWin | SERP-est |
| agent memory framework | 100–300 | 16 | QuickWin | SERP-est |
| open source agent memory | 100–300 | 12 | QuickWin | SERP-est |
| agent memory benchmark | 50–200 | 8 | QuickWin | SERP-est |
| agent memory comparison | 50–200 | 10 | QuickWin | SERP-est |
| persistent memory for agents | 50–200 | 14 | QuickWin | SERP-est |
| memory for autonomous agents | 50–200 | 12 | QuickWin | SERP-est |
| ai memory system | 100–300 | 22 | T3 | SERP-est |
| agents forget context | 20–100 | 6 | Strategic | SERP-est |

### 4.2 `epistemic-memory` (Tortoise core differentiator — 13 keywords)

| Keyword | Vol | Diff | Tier | Source confidence |
|---|---|---|---|---|
| epistemic memory | 0–50 | 5 | **Strategic** | SERP-est (near-zero competition) |
| epistemic memory ai | 0–20 | 4 | **Strategic** | SERP-est |
| epistemic memory vs semantic memory | 0–20 | 4 | **Strategic** | SERP-est |
| what is epistemic memory | 0–20 | 4 | **Strategic** | SERP-est |
| belief graph | 50–200 | 12 | QuickWin | SERP-est |
| belief graph database | 0–50 | 8 | **Strategic** | SERP-est |
| knowledge graph with confidence | 0–50 | 8 | **Strategic** | SERP-est |
| memory that tracks belief | 0–20 | 4 | **Strategic** | Domain |
| why agents believe | 0–20 | 4 | **Strategic** | Domain |
| agent memory with confidence | 0–20 | 4 | **Strategic** | Domain |
| epistemic knowledge graph | 0–20 | 5 | **Strategic** | SERP-est |
| claims and evidence memory | 0–20 | 4 | **Strategic** | Domain |
| memory for agents to remember why | 0–20 | 3 | **Strategic** | Domain (brand line) |

### 4.3 `knowledge-graph` (graph memory — 15 keywords)

| Keyword | Vol | Diff | Tier | Source confidence |
|---|---|---|---|---|
| knowledge graph | 20,000+ | 60 | skip (extreme comp) | SERP-est |
| knowledge graph for ai agents | 100–300 | 18 | QuickWin | SERP-est |
| knowledge graph rag | 500–1,000 | 24 | T3 | SERP-est |
| graph rag | 500–1,000 | 26 | T3 | SERP-est |
| graphrag vs rag | 100–300 | 14 | QuickWin | SERP-est |
| knowledge graph vs vector database | 300–700 | 20 | T2 | SERP-est |
| graph database vs vector database | 300–700 | 22 | T3 | SERP-est |
| graph based agent memory | 50–200 | 12 | QuickWin | SERP-est |
| knowledge graph memory | 100–300 | 16 | QuickWin | SERP-est |
| graph memory for agents | 50–200 | 12 | QuickWin | SERP-est |
| temporal knowledge graph | 200–500 | 24 | T3 | SERP-est |
| knowledge graph for llms | 100–300 | 16 | QuickWin | SERP-est |
| graph database for agent memory | 50–200 | 10 | QuickWin | SERP-est |
| knowledge graph tutorial | 300–700 | 22 | T3 | SERP-est |
| multi-hop reasoning | 200–500 | 20 | T2/T3 | SERP-est |

### 4.4 `semantic-memory` (facts — 9 keywords)

| Keyword | Vol | Diff | Tier | Source confidence |
|---|---|---|---|---|
| semantic memory | 1,000–2,000 | 28 | T3 (watch) | SERP-est |
| semantic memory ai | 50–200 | 12 | QuickWin | SERP-est |
| semantic memory vs episodic memory | 100–300 | 10 | QuickWin | SERP-est |

> **Orientation note (P2-4 resolution):** `semantic memory vs episodic memory` (§4.4) and `episodic memory vs semantic memory` (§4.5) are the SAME search phrase with reversed word order — kept deliberately as per-tag orientation mirrors (the semantic-memory post and the episodic-memory post each target the phrase from their side). The module mirrors this intent (§4.4/§4.5 arrays each carry their orientation). A post tagged with BOTH tags would emit both — the generator dedupes identical phrases across the selected tag set (contract, #1861).
| semantic memory for agents | 50–200 | 10 | QuickWin | SERP-est |
| semantic memory llm | 50–200 | 12 | QuickWin | SERP-est |
| what is semantic memory | 200–500 | 16 | QuickWin | SERP-est |
| fact memory ai | 50–200 | 10 | QuickWin | SERP-est |
| knowledge base for agents | 100–300 | 16 | QuickWin | SERP-est |
| semantic knowledge graph | 100–300 | 18 | QuickWin | SERP-est |

### 4.5 `episodic-memory` (events — 8 keywords)

| Keyword | Vol | Diff | Tier | Source confidence |
|---|---|---|---|---|
| episodic memory | 1,000–2,000 | 28 | T3 (watch) | SERP-est |
| episodic memory ai | 50–200 | 10 | QuickWin | SERP-est |
| episodic memory vs semantic memory | 100–300 | 10 | QuickWin | SERP-est |
| episodic memory for agents | 50–200 | 10 | QuickWin | SERP-est |
| event log for agents | 50–200 | 12 | QuickWin | SERP-est |
| what is episodic memory | 200–500 | 14 | QuickWin | SERP-est |
| session memory ai | 50–200 | 12 | QuickWin | SERP-est |
| conversation history for agents | 50–200 | 14 | QuickWin | SERP-est |

### 4.6 `mcp` (protocol intersection — 10 keywords)

| Keyword | Vol | Diff | Tier | Source confidence |
|---|---|---|---|---|
| mcp server | 100,000+ | 70 | skip (extreme comp) | SERP-est |
| model context protocol | 10,000+ | 55 | skip (extreme comp) | SERP-est |
| mcp memory server | 100–300 | 16 | QuickWin | SERP-est |
| mcp server for agents | 100–300 | 20 | T3 | SERP-est |
| mcp knowledge graph | 50–200 | 14 | QuickWin | SERP-est |
| mcp server memory | 50–200 | 12 | QuickWin | SERP-est |
| how to build an mcp server | 500–1,000 | 28 | T3 | SERP-est |
| mcp server tutorial | 300–700 | 26 | T3 | SERP-est |
| mcp memory | 50–200 | 14 | QuickWin | SERP-est |
| mcp vs api | 200–500 | 18 | QuickWin | SERP-est |

### 4.7 `self-hosting` (ops — 9 keywords)

| Keyword | Vol | Diff | Tier | Source confidence |
|---|---|---|---|---|
| self-hosted agent memory | 50–200 | 10 | QuickWin | SERP-est |
| self-hosted ai memory | 50–200 | 10 | QuickWin | SERP-est |
| falkordb | 200–500 | 8 | QuickWin | SERP-est |
| falkordb vs neo4j | 50–200 | 8 | QuickWin | SERP-est |
| what is falkordb | 50–200 | 6 | QuickWin | SERP-est |
| run ai memory locally | 50–200 | 10 | QuickWin | SERP-est |
| local llm memory | 50–200 | 12 | QuickWin | SERP-est |
| docker agent memory | 20–100 | 8 | QuickWin | SERP-est |
| self-hosted rag | 100–300 | 16 | QuickWin | SERP-est |

### 4.8 `retrieval` (getting memory back — 12 keywords)

| Keyword | Vol | Diff | Tier | Source confidence |
|---|---|---|---|---|
| hybrid search | 2,000–3,000 | 30 | T3 (watch) | SERP-est |
| vector search vs hybrid search | 100–300 | 12 | QuickWin | SERP-est |
| hybrid retrieval | 200–500 | 20 | T3 | SERP-est |
| reciprocal rank fusion | 100–300 | 18 | QuickWin | SERP-est |
| rag | 50,000+ | 65 | skip (extreme comp) | SERP-est |
| semantic search for agents | 50–200 | 14 | QuickWin | SERP-est |
| multi-hop retrieval | 50–200 | 16 | QuickWin | SERP-est |
| context engineering | 200–500 | 22 | T3 | SERP-est |
| retrieval for llm agents | 50–200 | 14 | QuickWin | SERP-est |
| vector database for agents | 100–300 | 18 | QuickWin | SERP-est |
| agent context retrieval | 50–200 | 12 | QuickWin | SERP-est |
| why agents hallucinate | 100–300 | 14 | QuickWin | SERP-est |

### 4.9 `belief-propagation` (engine — 11 keywords)

| Keyword | Vol | Diff | Tier | Source confidence |
|---|---|---|---|---|
| belief propagation | 500–1,000 | 26 | T3 | SERP-est |
| expectation propagation | 100–300 | 22 | T3 | SERP-est |
| belief propagation graph | 50–200 | 16 | QuickWin | SERP-est |
| confidence score ai | 100–300 | 18 | QuickWin | SERP-est |
| uncertainty in knowledge graphs | 50–200 | 16 | QuickWin | SERP-est |
| probabilistic knowledge graph | 50–200 | 18 | QuickWin | SERP-est |
| evidence propagation | 20–100 | 8 | Strategic | SERP-est |
| belief propagation for agents | 0–50 | 6 | Strategic | SERP-est |
| expectation propagation agents | 0–50 | 6 | Strategic | SERP-est |
| confidence propagation | 0–50 | 6 | Strategic | SERP-est |
| how do agents know what they know | 0–20 | 4 | Strategic | Domain |

### 4.10 `sessions` (writing memory — 7 keywords)

| Keyword | Vol | Diff | Tier | Source confidence |
|---|---|---|---|---|
| session capture ai | 20–100 | 8 | Strategic | SERP-est |
| conversation mining | 50–200 | 14 | QuickWin | SERP-est |
| agent session logging | 50–200 | 10 | QuickWin | SERP-est |
| mine conversations for insights | 20–100 | 10 | Strategic | SERP-est |
| turn memory agents | 0–50 | 6 | Strategic | Domain |
| episodic memory capture | 20–100 | 8 | Strategic | SERP-est |
| meeting memory ai | 100–300 | 14 | QuickWin | SERP-est |

### 4.11 `memory-systems` (comparisons — 12 keywords)

| Keyword | Vol | Diff | Tier | Source confidence |
|---|---|---|---|---|
| mem0 vs letta | 200–500 | 12 | QuickWin | SERP-est |
| mem0 vs zep | 200–500 | 12 | QuickWin | SERP-est |
| mem0 vs graphiti | 100–300 | 10 | QuickWin | SERP-est |
| letta vs zep | 100–300 | 12 | QuickWin | SERP-est |
| zep vs graphiti | 50–200 | 10 | QuickWin | SERP-est |
| cognee vs mem0 | 50–200 | 10 | QuickWin | SERP-est |
| letta vs mem0 | 100–300 | 12 | QuickWin | SERP-est |
| agent memory tools | 100–300 | 16 | QuickWin | SERP-est |
| best agent memory | 100–300 | 16 | QuickWin | SERP-est |
| agent memory platforms | 50–200 | 14 | QuickWin | SERP-est |
| open source agent memory comparison | 50–200 | 10 | QuickWin | SERP-est |
| memory layer for agents | 50–200 | 12 | QuickWin | SERP-est |

### 4.12 `provenance` (trust angle — 12 keywords)

| Keyword | Vol | Diff | Tier | Source confidence |
|---|---|---|---|---|
| ai provenance | 50–200 | 16 | QuickWin | SERP-est |
| provenance for ai agents | 20–100 | 10 | Strategic | SERP-est |
| source attribution ai | 20–100 | 10 | Strategic | SERP-est |
| traceable ai memory | 0–50 | 6 | Strategic | SERP-est |
| auditable ai memory | 0–50 | 8 | Strategic | SERP-est |
| where did the ai get that | 20–100 | 6 | Strategic | SERP-est |
| memory with source citations | 0–50 | 6 | Strategic | Domain |
| agent memory audit trail | 0–50 | 8 | Strategic | SERP-est |
| ai memory privacy | 50–200 | 14 | QuickWin | SERP-est |
| data provenance ai | 100–300 | 18 | QuickWin | SERP-est |
| trust in ai agents | 100–300 | 16 | QuickWin | SERP-est |
| explainable ai memory | 0–50 | 8 | Strategic | SERP-est |

**Totals (§4.1–4.12, taxonomy-tiered):** T1: 0 · T2: 2 · T2/T3 (borderline): 2 · T3: 14 · T3 (watch): 4 · QuickWin: 80 · Strategic: 30 · Skip (extreme comp): 4 · **= 136** · + §4.13 value-prop layer (28) = **164 analyzed** (module: 147 actionable).

### 4.13 Value-Prop Layer (2026-08-28, owner direction — four candidate value props → search language → keywords)

> Calibration: live SERP analysis 2026-08-28. Drift/stays-current = the conceded pain (competitors publish guides on it; Tony's A/B conceded "your drift point still beats us"; arscontexta ships a `/reseed` command). Learning = the 2026 research frontier (SelfMem/MemRL/ReMe/TMEM/WebCoach/Mem2Evolve). Autonomy = real volume, outcome framing. Smarter = commodity (skip as a hook; keep as consequence). Source confidence: `SERP-est` / `Domain`.

| Value prop | Keyword | Vol (SERP-est) | Diff | Tier | Source confidence |
|---|---|---|---|---|---|
| without drift / stays current | context rot | 200–500 | 12 | QuickWin | SERP-est |
| without drift / stays current | context rot ai agents | 50–200 | 8 | QuickWin | SERP-est |
| without drift / stays current | memory drift | 100–300 | 14 | QuickWin | SERP-est |
| without drift / stays current | ai agent memory drift | 20–100 | 8 | Strategic | SERP-est |
| without drift / stays current | why ai agents forget | 100–300 | 10 | QuickWin | SERP-est |
| without drift / stays current | why do agents forget | 50–200 | 8 | QuickWin | SERP-est |
| without drift / stays current | agent gets dumber over time | 20–100 | 6 | Strategic | SERP-est |
| without drift / stays current | stale ai memory | 20–100 | 8 | Strategic | SERP-est |
| without drift / stays current | ai agents get worse over time | 20–100 | 6 | Strategic | SERP-est |
| without drift / stays current | summarization drift | 20–100 | 8 | Strategic | SERP-est |
| without drift / stays current | behavioral state decay | 0–50 | 6 | Strategic | Domain (research term) |
| learns / stays current | self-evolving agents | 200–500 | 16 | QuickWin | SERP-est |
| learns / stays current | self-evolving agent memory | 50–200 | 10 | QuickWin | SERP-est |
| learns / stays current | self-correcting memory | 100–300 | 12 | QuickWin | SERP-est |
| learns / stays current | self-improving agents | 100–300 | 14 | QuickWin | SERP-est |
| learns / stays current | agents learn from experience | 50–200 | 8 | QuickWin | SERP-est |
| learns / stays current | agent improves over time | 50–200 | 8 | QuickWin | SERP-est |
| learns / stays current | memory that learns | 20–100 | 6 | Strategic | SERP-est |
| learns / stays current | continual learning agents | 100–300 | 18 | QuickWin | SERP-est |
| learns / stays current | runtime continuous learning | 0–50 | 6 | Strategic | Domain (research term) |
| increases autonomy | agent autonomy | 300–700 | 22 | T3 | SERP-est |
| increases autonomy | autonomous agent memory | 50–200 | 10 | QuickWin | SERP-est |
| increases autonomy | increase agent autonomy | 20–100 | 8 | Strategic | SERP-est |
| increases autonomy | agents that need less supervision | 0–50 | 6 | Strategic | Domain |
| makes agents smarter | smarter ai agents | 100–300 | 14 | QuickWin | SERP-est |
| makes agents smarter | make ai agents smarter | 50–200 | 10 | QuickWin | SERP-est |
| makes agents smarter | improve agent performance with memory | 20–100 | 8 | Strategic | SERP-est |
| makes agents smarter | memory instead of model scale | 0–50 | 6 | Strategic | Domain (ReMe result: memory substitutes model scale) |

**§4.13 tally (28 rows):** QuickWin: 15 · Strategic: 12 · T3: 1 — of which **4 are Domain-content-only (not injected)**: behavioral state decay, runtime continuous learning, agents that need less supervision, memory instead of model scale (research/Domain vocabulary; subset of the 12 Strategic). Injected into module: 24.

---

## 5. Strategic Tier Rationale (deviation from ElDato, accepted in scoping)

ElDato skipped zero-volume keywords (`Skip — Zero Volume: 454`). **Tortoise deliberately inverts this** for category-defining terms:

- **Why:** Tortoise's moat is *epistemic* memory — a position NO competitor claims (all incumbent memory systems position on *what* is remembered: facts (Mem0), time (Graphiti), agent control (Letta), documents (Cognee)). The terms that describe the moat ("epistemic memory", "belief graph", "claims and evidence memory", "memory that tracks belief") have near-zero reported volume **because the category doesn't exist yet** — zero-volume ≠ zero-value when the goal is category creation.
- **Intent quality:** these terms are high-intent (a searcher typing "epistemic memory ai" is a builder actively looking for this exact capability) and low-competition (SERP empty or near-empty — verified 2026-08-28).
- **Mechanics:** first-mover content on Strategic terms compounds into authority that later volume (when the category grows) converts. The same playbook as "agent memory" circa 2024 — Mem0 wrote the category term before the volume existed.
- **Guard:** Strategic keywords are NOT injected into meta generation as primary targets (low immediate search volume); they shape *content* (title/H2/body language) and the product's naming vocabulary. Quick Win/Tier 2/3 keywords carry the meta-title injection duty in #1861. **Reconciliation with the module:** the module carries Strategic terms so the generator can *choose* them when composing content-driven meta, but the prompt builder ranks QuickWin/Tier 2/Tier 3 above Strategic for the primary target keyword; Strategic terms feed the title/body vocabulary, not the primary meta keyword slot.

---

## 6. SERP Analysis (Tier 1-equivalent + Strategic only — proportional per scoped plan)

| Cluster | Who ranks (2026-08-28) | Content format that wins | Tortoise gap |
|---|---|---|---|
| agent memory (head) | IBM Think, Mem0 blog, arXiv surveys, Redis, kotrov.com guide | Definition explainers + framework guides | Nobody owns *belief/confidence* angle; nobody positions "memory is learning, not recall" |
| graph vs vector | Hindsight, machinelearningmastery, atlan, digitalapplied, dreaming.press | Comparison/decision posts (recent, Aug 2026 heavy) | The false dichotomy — Tortoise's claims-with-confidence third position is unclaimed |
| knowledge graph rag | Microsoft (GraphRAG), redis.io, mindstudio, fifthelement | Vendor + explainer | GraphRAG is batch/static-corpus; Tortoise is incremental agent memory — differentiation open |
| mcp memory server | modelcontextprotocol.io (spec), tech-insider (setup guides) | Spec docs + setup tutorials | "MCP memory server" content is thin (OpenMemory/Graphiti MCP are new) — intersection underserved |
| self-hosted memory | hindsight.vectorize.io, digitalapplied, devtoollab, nomadlab | Comparison/self-host guides | Tortoise's Docker+FalkorDB single-daemon story is a concrete, credible self-host angle |
| belief propagation / EP | Academic (Minka EP paper, AAAI BIKG, Springer UKGEBN) | Papers, no product blogs | **Zero product-lens content** — wide-open gap for "EP for agent memory" explainer |
| memory system comparisons | dreaming.press, devtoollab, theaiengineer, nomadlab, digitalapplied | Head-to-head tables (very frequent publishing) | Tortoise is never in the comparison tables (except as FalkorDB mention) — an entry piece is the wedge |

**Format takeaways:** (1) comparison/decision posts dominate and are most shared; (2) definition posts rank but are commodity; (3) the "unresolved debate" posts (graph vs vector) get the most engagement — Tortoise should publish *into the debate*, not around it; (4) no product-blog owns belief propagation — first-mover advantage is real.

---

## 7. Adversarial Review Gate (research skill — disconfirming queries + resolutions)

Per scoping plan step 7. Run as a fresh-context adversarial pass; findings below.

### A1 — "Knowledge graphs lose to vector stores on cost/accuracy" (2026 benchmarks)
**Disconfirming query run:** *"is a knowledge graph worth it for agent memory — criticism, when vector store is enough"*
**Evidence surfaced:**
- Medium postmortem (2026-08): graph ingest ~$14 vs $0.03 embeddings for one user history; graph still scored lower on recall; retrieved 6× more context for a worse answer.
- Mem0 removed its graph module in v3 (April 2026, commit a488e190) — the Mem0 paper showed the graph variant barely won overall (68.44 vs 66.88) while losing single/multi-hop, running 3× slower and 2× tokens.
- Counter-evidence (same corpus): the structured winner in the benchmark was still doing "graph-shaped work" (atomic facts + validity windows); the temporal regression failure (41% wrong on "what was true at time T") is a *time* problem, not a topology problem — and a graph made the fix a one-liner.
**Resolution:** The epistemic layer does not require heavy LLM entity-extraction at write time (Tortoise stores *claims* + confidence — extraction is claims from sessions, not full entity/relation graphs à la Graphiti). The "graph = expensive" critique targets entity-graph construction cost; Tortoise's claims-as-Points model sits at the cheap end. **Content must pre-empt this objection** (title/body: "memory that knows why, without the extraction bill"). Do NOT publish naive "graphs beat vectors" content — the debate is live and the anti-graph side has real numbers.

### A2 — "Agent memory is product state, not magic" (scope/trust critique)
**Disconfirming query run:** *"agent memory — is it just a vector store / is it overhyped"*
**Evidence surfaced:** Clord (2026-05): "Agent Memory Is Product State, Not Magic" — memory needs source/scope/owner/freshness/delete/conflict/audit; "The worst memory is almost-right memory"; agent memory without receipts = "haunted notebook".
**Resolution:** This validates Tortoise's provenance/source-tier/status-lifecycle model (Source nodes, T0–T4 tiers, supersede/invalidate, aboutEdges). Content angle: **Tortoise is the "receipts" memory** — the audit-trail/claims-with-evidence position is the differentiated answer to the "memory is state" critique. Aligns with §5 (provenance Strategic tier).

### A3 — "Vector databases are the right default; graphs are overkill for most agents"
**Disconfirming query run:** *"vector database enough for agent memory — when not to use a graph"*
**Evidence surfaced:** machinelearningmastery, digitalapplied, knowlee all conclude most agents should start vector + episodic and only add graph when multi-hop becomes a recurring failure mode; atlan adds governance as the third axis.
**Resolution:** Accept the default-for-most claim — Tortoise's answer is "the epistemic layer is orthogonal to the store choice": you can run Tortoise's claims/confidence on top of any durable store, and the graph structure is what makes contradiction/provenance first-class. Content must not overclaim "every agent needs a graph."

### A4 — "MCP is a crowded, protocol-owned space; blog can't win head terms"
**Disconfirming query run:** *"MCP — is the blog content saturated / can a small vendor rank"*
**Evidence surfaced:** modelcontextprotocol.io owns spec + tutorial head terms; 2026 guide content (tech-insider, ai-agent-guidebook) is dense; the intersection (memory × MCP) is thin — only OpenMemory/Mem0 MCP, Graphiti MCP, and vendor posts.
**Resolution:** skip head MCP terms (documented as skip), target `mcp memory server` / `mcp knowledge graph` / `mcp server memory` intersection (QuickWin, low-diff, thin SERP). Confirmed by SERP: "mcp memory server" results are sparse and new.

### A5 — "Volumes are estimates, not Keyword Planner data" (data-quality challenge)
**Disconfirming question run internally:** *is every number in this doc a real measured volume?*
**Resolution:** No — Planner access was unavailable (C1 pending). All volumes are SERP-calibrated bands, tagged `SERP-est`, with the v2 refresh path (GSC + Planner/DataForSEO) documented. This is a *documented limitation*, not hidden — the tier assignments (the durable output) are robust to ±50% volume error because they are dominated by the *competition* signal (SERP player density), which is measured, not estimated. Strategic-tier assignments additionally rest on observed SERP emptiness (verified per-cluster), not volume numbers.

---

## 8. Data Sources + Confidence Register

| Source | Used for | Status | Confidence |
|---|---|---|---|
| GSC (sc-domain:tortoise.premiselabs.co) | Query seed | **EMPTY** (verified 2026-08-28: 0 clicks/0 impressions all ranges; homepage indexed, 0 of 10 sitemap URLs indexed) | — (documented; v2 refresh path) |
| Live SERP analysis (exa/web, 2026-08-28) | Difficulty signal, format winners, gaps | Used for every cluster | High (observed) |
| Domain taxonomy (ONTOLOGY.md, website, epic scope) | Seed terms, tag taxonomy, Strategic terms | Used | High (primary source) |
| Google Ads Keyword Planner | Volumes | **Not configured** (C1 pending) | — |
| DataForSEO / Ubersuggest | Volumes | Not used (paid fallback, deferred) | — |

**Refresh trigger (v2, ~6 months):** GSC query export as primary seed → Keyword Planner/DataForSEO volumes → re-tier. New blog posts will accrue query data starting now (0 published posts as of findings date).

---

## 9. No-ElDato-Leak Guard (verification checklist item)

- Seed list built from **ontology + product surfaces only** (§2) — no tourism/marketplace vocabulary by construction.
- Full keyword corpus grepped for ElDato cluster terms: `riviera maya`, `playa del carmen`, `cenote`, `beach club`, `deals`, `restaurant`, `hotel`, `city names (cancun/tulum/cozumel/puerto aventuras)`, `tulum`, `playa` → **0 matches**.
- `seo-keywords.ts` module keys = the 12 taxonomy tags above; values verified to contain no marketplace terms.

---

## 10. Deliverables Map (per scoped plan §8)

| Artifact | Path | Status |
|---|---|---|
| Master doc | `docs/research/2026-08-28-tortoise-blog-keywords/research.md` (this file) | ✅ |
| Docs index pointer | `docs/00_index.md` row | ✅ |
| Machine module | `website/functions/blog/_shared/seo-keywords.ts` (`TagKeywords`, `TAG_KEYWORDS` — actionable arrays only; tiers/evidence live in this doc) | ✅ |
| Contract vs #1861 | module shape matches `import { TAG_KEYWORDS } from "../_shared/seo-keywords"`; content-driven fallback when a tag has no entry (see module docstring) | ✅ |

**Consumed by:** #1861 (keyword injection into generate-seo prompt builder), future content-pipeline automation (deferred epic), blog content strategy.
