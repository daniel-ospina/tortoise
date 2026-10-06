---
title: "Subgraph retrieval — external research for the reader-context redesign"
type: data
domain: data
status: live
doc_status: live
created: 2026-09-11
updated: 2026-09-11
ownedBy: epistemic-team
subjects:
  team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: Point, Operator, Session, Source
related:
  - "#2976 (oracle ceiling = 81%; the 0/52 is a retrieval defect)"
  - "#2978 (content-less operator points consume 61% of context slots)"
  - "#3011 (the A/B/C experiment this research feeds)"
  - "#2578 (the measurement that surfaced both)"
---

# Subgraph retrieval — external research

**Question.** Retrieval currently matches individual graph nodes and hands the reader a
**flat list of bare node texts** — edges discarded. Should it instead retrieve and
present the **small connected subgraph** around matched nodes (the claim plus what it
implies / contradicts / is-about / came-from), rendered *with* its relationships?

**Verdict from the evidence: yes — with explicit expansion limits, edge-priority
ranking, hub damping, and a flat-path fallback for simple lookups.**

---

## 1. What the systems do

| System | How it chooses the subgraph | Budget control |
|---|---|---|
| **Microsoft GraphRAG** (local search) | entity-anchored: top-N entities (2× oversample) → fan out to linked text units, relationships, community reports | **one fixed window** (`max_tokens=12000`: ~50% raw text, ~15% community reports (0.5/0.15/0.35)); relationships admitted **in-network first, ordered by `combined_degree`** [MEDIUM] |
| **LightRAG** | low-level (entity keys) → matched entities + **1-hop relations**; high-level (relation keys) → relation vector search; hybrid = both, deduped | one cheap key-extraction LLM call [MEDIUM] |
| **HippoRAG** | **Personalized PageRank** seeded at query-linked entities; returns top-ranked **passages** — nodes/triples are internal seeding only | matches iterative IRCoT at **10–30× lower cost**; multi-hop gains up to +20% [MEDIUM] |
| **Zep / Graphiti** | three scopes (edges = dated facts with `valid_at`/`invalid_at`; nodes = entity summaries; communities); fuses cosine+BM25+BFS via RRF; BFS "land and expand" from other legs' hits | **`MAX_SEARCH_DEPTH=3`**, candidate cap `2*limit`, node-distance reranker; base retrieval zero-LLM [MEDIUM] |
| **Mem0 (OSS)** | **no traversal** — entity matches only *boost* scores on a vector-gated pool | **hub penalty** damping high-degree entities [MEDIUM] |

**Consensus:** every non-parametric graph retriever pairs expansion with a
**prune / rank / filter** stage. Expansion breadth is bounded by what you then admit. [MEDIUM]

## 2. How to serialize it

**For fact questions, unordered linearized triples outperform fluent natural-language
text** — counter-intuitive but directly measured. [MEDIUM]
Triple-vs-triple format gaps are marginal. [LOW]
LongMemEval's own reader uses timestamp-sorted **structured JSON + a `Current Date:`
header** (~+10 pts claimed). [LOW]

⚠️ **Absence:** no published head-to-head A/B of *"the same nodes as a flat list"* vs
*"the same nodes with edges rendered"*. The triples>text result is the best available
proxy, not the exact experiment. [ABSENCE]

## 3. Hops, explosion control, budget

- **Expand 1 hop from strong seeds, then prune** — NVIDIA does 1 hop then vector similarity
  + **PCST (prize-collecting Steiner tree)** pruning. [MEDIUM]
- **3–4 hop ceiling** — deeper traversal scales latency exponentially. [LOW]
- **PPR is the principled ranking operator** for "which reachable nodes deserve context". [MEDIUM]

## 4. Documented failure modes (adversarial)

| Failure | Evidence |
|---|---|
| **GraphRAG can lose to vanilla RAG** | **−13.4%** accuracy on Natural Questions, **−16.6%** on time-sensitive questions; only +4.5% on HotpotQA multi-hop for ~2.3× latency [MEDIUM] |
| **Extraction noise propagates** | KAG cites OpenIE noise as GraphRAG's core weakness; DEG-RAG reports removing **~40%** of entities and relations *improves* QA [LOW] |
| **Hub bias** | GraphRAG admits high-`combined_degree` nodes first; Mem0 counters with a hub penalty; type-blind BFS drifts topic [MEDIUM] |
| **Near-relevant distractors** | the top killer — answerless-but-retrieved content actively harms [LOW] |
| **Wrong local/global mode** | over-summarizes or under-reaches [MEDIUM] |

## 5. Agent memory / LongMemEval numbers

Zep/Graphiti **63.8%** vs Mem0 **49.0%** (competitor-run eval — Vectorize, the vendor of Hindsight). [MEDIUM]
Zep self-reports +18.5%/gpt-4o over baselines. [LOW — self-reported]
Mem0 self-reports **93.4%** vs Zep **71.2%** on LongMemEval — both from Mem0's own Zep-vs-Mem0 comparison page (GPT-4o, April 2026); Mem0's current research-hub self-report is **94.4%**, with no contemporaneous Zep figure. [LOW — self-reported]

⚠️ Vendor and competitor-run LongMemEval numbers disagree by **~2×**, so
*"graph beats flat by X%"* is **not settleable from published results.**

---

## Design implications

1. **Anchor on the matched claim; expand ≤1 hop by default.** Edge priority:
   `aboutObject` (identity) → supersession/validity → `NAND` (contradiction) → `IMPL`.
   A second hop only *through* an `aboutObject` hub to sibling claims — never a general 2-hop walk.
2. **Render labeled, directed, dated relations**, not bare nodes:
   `"C2 is about Entity E"`, `"C1 IMPLIES C3"`, `"C4 CONTRADICTS C1"`,
   `"C1 came from session S (date D)"`.
3. **Bounds:** ~10–15 candidates per anchor; hard token budget ~8–12k; dedupe across
   anchors; **damp hubs** (Mem0-style) so a high-degree entity cannot dominate.
4. **Rank the admitted subgraph**: `seed similarity × edge-priority × hop decay`,
   in-network before out-of-network — PPR-like reachability, not unweighted BFS.
5. **Mark superseded/contradicted claims explicitly.** Never leave contradiction implicit
   — conflicting evidence triggers reader hedging/refusal.
6. **Keep a flat path for simple lookups.** The −13.4% NQ result is the warning: graph
   assembly is for multi-hop / temporal / contradiction questions, not simple fact lookup.
7. **⛔ Prerequisite:** *"if the needed claim is never retrieved, subgraph rendering cannot
   fix it."* An **evidence-recall check must precede** any rendering change.

## Contradictions / open questions

- **Serialization**: triples beat fluent text for fact questions [8] vs GraphRAG's default
  of community-summary *prose*. Likely task-dependent; untested on personal-memory claims.
- **LongMemEval**: numbers unreconciled (~2× disagreement).
- **For Tortoise specifically**: how much of `0/52` is *retrieval-miss* vs
  *serialization-loss*? Unmeasured. The #2978 finding (61% of context slots are empty
  operator nodes) and the #2976 oracle ceiling (81%) bracket it, but the split is unknown.

## Sources

1. https://microsoft.github.io/graphrag/
2. https://www.microsoft.com/en-us/research/blog/graphrag-improving-global-search-via-dynamic-community-selection/
3. https://arxiv.org/abs/2410.05779 (LightRAG)
4. https://arxiv.org/abs/2405.14831 (HippoRAG) · https://arxiv.org/abs/2502.14802 (HippoRAG 2)
5. https://github.com/getzep/graphiti/blob/main/graphiti_core/search/search_config.py · https://blog.getzep.com/how-do-you-search-a-knowledge-graph/
6. https://github.com/mem0ai/mem0/blob/main/mem0/memory/main.py
7. https://arxiv.org/html/2408.08921v1 (Graph RAG: A Survey)
8. https://openreview.net/forum?id=sb4cHMf8OV (linearized triples vs fluent text)
9. https://arxiv.org/html/2604.27713v1
10. https://arxiv.org/html/2505.08504v1
11. https://agentmemorybenchmark.ai/dataset/longmemeval
12. https://developer.nvidia.com/blog/boosting-qa-accuracy-with-graphrag-using-pyg-and-graph-databases/
13. https://learn.microsoft.com/en-us/azure/horizondb/ai/graph-rag
14. https://aclanthology.org/2026.acl-long.714.pdf
15. https://arxiv.org/html/2506.05690v3 (When to use Graphs in RAG)
16. https://arxiv.org/abs/2401.05856 (Seven Failure Points in RAG)
17. https://vectorize.io/articles/mem0-vs-zep
18. https://particula.tech/blog/agent-memory-frameworks-tested-mem0-zep-letta-cognee-2026
19. https://arxiv.org/pdf/2501.13956 (Zep paper)
20. https://mem0.ai/blog/zep-vs-mem0-which-ai-memory-layer-should-you-choose
