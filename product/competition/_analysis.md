# Competitive Analysis — organisation-design-team

> Auto-bootstrapped by competitor-research skill. Synthesis of competitor profiles in this directory.

---

## 1. Competitive Threat Map

### Tier 1 — Watch Closely

| Competitor | Why Tier 1 | Specific relevance | What to watch for |
|---|---|---|---|
| **Zep** | Direct overlap in memory architecture space — temporal knowledge graphs, FalkorDB backend, agent memory infrastructure | Graphiti (open-source KG engine) uses FalkorDB — same graph DB chosen in our ADR-004. Zep's managed cloud + enterprise governance model is a possible reference architecture for our epistemic graph. Their benchmark results (LoCoMo 94.7%) set the bar for agent memory accuracy. | Zep launches a self-hosted enterprise tier that competes with our “build your own” approach; Graphiti adds native belief propagation or confidence scoring that overlaps with our epistemic layer design |
| **Hindsight (Vectorize)** | Direct overlap in agent memory space — TEMPR 4-way hybrid retrieval, observation consolidation, 40+ framework integrations, LongMemEval SOTA (91.4-94.6%) | PostgreSQL + pgvector backend (same infra choice as us). LLM-at-write memory extraction — the key architectural trade-off vs our verbatim approach. MIT license, no feature walls. Benchmark leadership (91.4% LongMemEval vs Zep's 71.2%) sets the highest bar. | Hindsight adds belief propagation or confidence scoring; closed-source cloud diverges from MIT OSS; LLM-at-write becomes cheaper (threatening our verbatim cost advantage) |

### Tier 2 — Monitor

*No Tier 2 competitors yet.*

### Tier 3 — Low / No Threat

| Competitor | Why Tier 3 |
|---|---|
| **Clarity (heyclarity.dev)** | AI consulting firm + pre-GA Self-Model API (130 endpoints, beliefs + confidence scoring). Conceptually adjacent (self-models, belief modeling, context assembly) but no product adoption, no community, single customer case study. Two-person bootstrapped consultancy. Monitor if API reaches GA with pricing — currently no threat. |
| **Project Alexandria (MSR)** | **Decommissioned.** Probabilistic KB construction (AKBC 2019 Best Paper). Productized as Viva Topics → retired Feb 2025. Superseded by LLM-based approaches (Copilot, GraphRAG). Historical reference — what Microsoft tried before LLMs. |

---

## 2. Strategic Implications

### What Zep's architecture means for our epistemic graph

**1. Temporal knowledge graphs are the right paradigm.**
Zep's core insight — track *when* facts were true, not just *what* is true — validates our decision to build an epistemic graph with temporal validity (ADR-004 §3: bi-temporal fact tracking). Their benchmark results (LoCoMo 94.7%, LongMemEval 90.2%) prove this approach outperforms vector-only or state-machine alternatives.

→ **Data points:** [Profile §6] Temporal memory with `valid_from`/`valid_to`; [Profile §8] benchmark leadership on both industry-standard tests.

**2. FalkorDB is production-validated for this workload.**
Zep chose FalkorDB as a Graphiti backend — the same graph DB in our ADR-004. Their 100M-graph benchmark (168ms P95 retrieval) confirms FalkorDB handles the scale we need. Their contributor list (AWS, Microsoft, Neo4j) signals ecosystem buy-in.

→ **Data points:** [Profile §6] Graphiti supports Neo4j, FalkorDB, AWS Neptune; [Profile §8] 35+ contributors including AWS, Microsoft, FalkorDB, Neo4j.

**3. The managed cloud model is a different path from ours.**
Zep monetizes via credit-based SaaS with enterprise compliance (SOC 2, HIPAA, BYOC). Our epistemic graph is infrastructure we build and operate, not a product we sell. Zep's pricing (from $0 to $375+/mo) is a useful reference for what the market will pay for governed agent memory — but our cost structure is different (self-operated, not SaaS margin).

→ **Data points:** [Profile §5] Pricing tiers from Free to Enterprise; [Profile §5] BYOC/BYOK deployment options.

**4. Their "Context Lake" pattern matches our multi-agent epistemic design.**
Zep's "millions of context graphs, managed as one system" maps to our vision of multiple agents contributing to shared epistemic claims. Their ABAC (Principal → Resource → Action → Policy) is how we'd handle team-level access to claims with different confidence.

→ **Data points:** [Profile §6] Context Lake architecture; [Profile §6] ABAC access control with Allow/Deny policies.

**5. Small team risk is real but not relevant to us.**
Zep is 5 people. We're not competing with them — we're learning from their architecture. Their ability to ship SOC 2 + HIPAA + 20K-star OSS with 5 people suggests the infrastructure layer is simpler than it appears, or they're exceptionally efficient. Either way, it lowers the perceived barrier to building a production-grade memory system.

→ **Data points:** [Profile §1] 5 employees; [Profile §6] SOC 2 Type II, HIPAA BAA, managed cloud with 100M-graph benchmarks.

---

## 3. Feature Comparison

| Capability | Zep / Graphiti | Our Epistemic Graph (planned) | Notes |
|---|---|---|---|
| Temporal fact tracking | ✅ `valid_from`/`valid_to` | ✅ ADR-004 §3 | Same paradigm |
| Graph DB backend | FalkorDB, Neo4j, Neptune | FalkorDB (ADR-004) | Same primary choice |
| Belief propagation | ❌ Not built | ✅ Core feature | Our differentiator |
| Confidence scoring | ❌ Not built | ✅ 0.0-1.0 with auto-update | Our differentiator |
| Multi-agent claims | ✅ Via ABAC (per-graph) | ✅ Shared claims + aggregation | Architectural difference: Zep isolates graphs; we share them |
| Enterprise compliance | ✅ SOC 2, HIPAA, audit | ❌ Not needed (internal infra) | Different use case |
| Open source | ✅ Graphiti (Apache-2.0) | N/A (internal system) | |
| Managed cloud | ✅ Credit-based SaaS | ❌ Self-operated | Different model |
| Provenance | ✅ Every fact → source episode | ✅ Core feature | Same pattern |
| MCP server | ✅ zep-mcp-server | Planned | |

---

## 4. Key Learnings for Our Architecture

1. **Zep validates our FalkorDB choice.** Their 100M-graph benchmark at 168ms P95 proves FalkorDB scales for temporal knowledge graphs. ADR-004's selection is vindicated by independent production use.

2. **Our differentiator is belief propagation + confidence scoring.** Zep tracks facts temporally but doesn't propagate confidence changes or aggregate multi-agent ratings. These are the epistemic graph's core value — Zep's absence of them confirms we're building something distinct, not duplicating.

3. **Provenance is non-negotiable.** Zep's "every fact traces back to source episode" is table stakes for agent memory. Our epistemic graph must maintain the same standard — every claim must link to its evidence source.

4. **The market ceiling is real.** Zep's S&P coverage ("de facto partner in enterprise agent stack") and 20K GitHub stars signal that agent memory infrastructure is a real category with enterprise demand. Our internal system doesn't need to capture that market — but the architectural patterns are the same.

5. **Observations (pattern detection) is the next frontier.** Zep's "Observations" feature — analyzing graph structure to surface patterns — is something we should consider for the epistemic graph. "Claims about organic ROI have been contradicted 3 times in 6 months" is a pattern worth surfacing.

---

*Last updated: 2026-07-06*

---

## 5. Architecture Deep-Dive

> Phase 6 — conditional. Run because Zep's architecture is directly relevant to our epistemic graph design (ADR-004).

### 5a — Process/Component Map

```
┌──────────────────────────────────────────────────────────────────┐
│                      1. INGEST (Episodes)                         │
│  Chat messages, JSON, business data, user interactions            │
│  Python/TS/Go SDKs, REST API, MCP server                          │
│  Synchronous write, 1 credit per 350 bytes                        │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                2. CONTEXT GRAPH ENGINE (proprietary)              │
│  Built on Graphiti (open-source temporal KG)                      │
│  Entity extraction, relationship inference, fact invalidation     │
│  Bi-temporal tracking: valid_from / valid_to                      │
│  Backend: Neo4j / FalkorDB / AWS Neptune (configurable)          │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                   3. CONTEXT LAKE (proprietary)                    │
│  Millions of context graphs, managed as one system                │
│  Multi-tenant isolation, ABAC, retention policies, audit logs     │
│  Observation engine: pattern mining on graph structure            │
│  Sub-200ms P95 retrieval at any scale (tested to 100M graphs)     │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                      4. RETRIEVAL & OUTPUT                         │
│  Hybrid: vector + full-text + graph traversal                     │
│  REST API (/graph/search, /entity/search), SDKs, MCP server       │
│  Context injection: thread.get_user_context()                      │
│  Chat: dialectic agent loop with tool-based graph traversal       │
└──────────────────────────────────────────────────────────────────┘
```

**Component breakdown:**

| Component | What it does | Data in | Data out | Trigger |
|-----------|-------------|---------|----------|---------|
| **Ingest** | Accept any data object (chat, JSON, text) as Episodes | Raw messages, business data, user actions | Structured Episode objects with token count | API call (sync) |
| **Context Graph Engine** | Extract entities, relationships, facts; track temporal validity | Episodes | Knowledge graph (entities + edges + facts with valid_from/to) | Async per Episode batch |
| **Context Lake** | Govern multi-tenant graphs at scale; mine patterns | Knowledge graphs from Engine | Managed, access-controlled graphs with Observations | Continuous (engine output) |
| **Retrieval** | Hybrid search + context assembly for agent queries | Agent query + thread/user context | Token-efficient context block | API call (sync, sub-200ms) |

### 5b — Design Choice Tables

| Decision | Choice | Rationale | What they optimize for |
|----------|--------|-----------|----------------------|
| **Primary graph DB** | Neo4j / FalkorDB (native graph), NOT PostgreSQL+pgvector | Temporal knowledge graphs need native graph traversal — recursive CTEs degrade past depth 7. Graphiti supports multiple backends for flexibility. | Query depth + ecosystem lock-in avoidance. Graph-native traversal is O(path_length); Postgres recursive CTEs are exponential. |
| **Graph engine license** | Apache-2.0 open source (Graphiti) with proprietary cloud (Zep) | Open-source builds developer trust + community contributors (AWS, Microsoft). Cloud monetizes the governed, compliant version. | Developer adoption × enterprise revenue. The open-core model: free engine → paid governance. |
| **Multi-backend support** | Neo4j, FalkorDB, AWS Neptune — configurable, not locked to one | Enterprise customers have existing DB relationships. Lock-in to one graph DB reduces TAM. | Enterprise adoption (BYO infrastructure). |
| **Credit-based pricing** | Per-Episode-byte credits, not per-query or per-seat | Predictable cost for users (budget per message volume). Scales linearly with usage. Avoids surprise bills from complex queries. | Cost predictability for enterprise buyers. |
| **ABAC at substrate** | Principal → Resource → Action → Policy at the graph layer, not app middleware | Governance can't be bolted on — every query, every graph, every layer enforces policy. SOC 2 / HIPAA require this. | Enterprise compliance (SOC 2 Type II, HIPAA BAA). |
| **Provenance preserving** | Every fact traces back to source Episode | Audit trail is non-negotiable for regulated industries. "Why does the agent think this?" must be answerable. | Compliance + trust. Same pattern we need for epistemic graph. |
| **Bi-temporal validity** | Facts have `valid_from` / `valid_to`; old facts stay as history | Agents need to know what was true at a point in time, not just what's true now. "When did the user change their preference?" | Temporal reasoning accuracy. This is table stakes for agent memory. |
| **Python-first (64% Python)** | Graphiti is Python-native; Go for performance-critical paths (19%); TypeScript for SDK (14%) | Python dominates the AI/ML ecosystem. Go handles the hot path (API server, retrieval). TypeScript covers the web developer market. | AI ecosystem compatibility + performance where it matters. |
| **Observations engine** | Pattern mining on graph structure — recurrences, co-occurrences, anomalies | Goes beyond fact extraction. "Jane upgrades within 2 weeks of every product launch" is a pattern, not a fact. | Insight quality — agents that understand patterns, not just facts. |
| **Community Edition discontinued** | Legacy code moved to `legacy/` folder; cloud-only going forward | Maintaining two codebases (OSS CE + Cloud) fragments engineering effort. Cloud revenue funds development. | Engineering focus + revenue. Risk: alienates self-hosted community. |

### 5c — Hidden Architecture

#### The uncomfortable truth

Zep's website says "Context Graph Engine" and "Context Lake" — but these are proprietary black boxes. The **real architecture is visible in Graphiti**, the open-source engine. Zep Cloud is Graphiti + governance layer.

**What's hidden:**

**1. Graphiti IS the architecture.**
Zep markets "Context Graph Engine" as proprietary secret sauce. But Graphiti (`github.com/getzep/graphiti`, 20K+ stars) is the actual implementation:
- Entity extraction from Episodes
- Relationship inference with temporal validity
- Fact invalidation when new data contradicts old facts
- Hybrid search (vector + full-text + graph traversal)
- Community detection

The "Context Graph Engine" is Graphiti with proprietary scaling, multi-tenancy, and governance. The core intelligence is open-source.

**2. The "Context Lake" is a multi-tenancy + governance layer.**
The Context Lake adds what Graphiti lacks:
- Multi-tenant isolation (Graphiti is single-subject)
- ABAC (attribute-based access control)
- Retention policies + Legal Hold
- Audit logs
- Built-in observability dashboard

These are not graph operations — they're infrastructure operations. Zep's proprietary layer is ops, not AI.

**3. No Postgres — they went graph-native from day one.**
Unlike Honcho (which hides graph ops in Postgres JSONB + LLM tool calls), Zep chose Neo4j/FalkorDB as the primary store. This means:
- Graph traversal is O(path_length), not exponential
- No "hidden graph" — the architecture is honest about what it is
- Trade-off: new infrastructure (graph DB) vs. Honcho's "use what you already have" (Postgres)

**4. The Observations engine is the "Dreamer" equivalent.**
Honcho has a Dreamer that runs during idle periods, analyzing conclusions for patterns. Zep's Observations does the same thing but continuously — analyzing graph structure to surface recurrences, co-occurrences, and anomalies. It's not a separate process; it's baked into the Context Lake.

#### The actual architecture: Graphiti + Governance

```
┌─────────────────────────────────────────────────────┐
│                  ZEP CLOUD (proprietary)             │
│  ┌───────────────────────────────────────────────┐  │
│  │         GOVERNANCE LAYER                       │  │
│  │  ABAC · Retention · Audit · Multi-tenancy     │  │
│  │  Observations engine · Observability dashboard│  │
│  └───────────────────────────────────────────────┘  │
│                       │                              │
│  ┌────────────────────▼──────────────────────────┐  │
│  │            GRAPHITI (open-source)              │  │
│  │  Entity extraction · Relationship inference   │  │
│  │  Temporal validity · Fact invalidation        │  │
│  │  Hybrid search · Community detection          │  │
│  └───────────────────────────────────────────────┘  │
│                       │                              │
│  ┌────────────────────▼──────────────────────────┐  │
│  │       GRAPH DB (Neo4j / FalkorDB / Neptune)    │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

#### Why this works for Zep (and only Zep)

| Condition | Zep's reality |
|-----------|---------------|
| **Graph depth** | Deep — temporal reasoning requires multi-hop traversal. "What did the user prefer before the product launch?" = 3-5 hops. Graph-native required. |
| **Graph breadth** | Wide — millions of graphs per deployment. Multi-tenancy at enterprise scale. |
| **Query pattern** | Hybrid — agentic (LLM decides what to search) + algorithmic (Observations mines graph structure). |
| **Latency budget** | Strict — sub-200ms P95. Graph-native traversal is linear; Postgres CTEs would miss this. |
| **Revenue model** | Enterprise SaaS — can afford graph DB infrastructure costs. Passes cost to customers. |
| **Open-source strategy** | Graphiti as developer funnel → Zep Cloud as monetization. Not "use what you already have" — "here's the engine for free, pay for governance." |

### 5d — Gap Consolidation

| Gap | Detail |
|-----|--------|
| **Pricing page** | `help.getzep.com/pricing` returned 404. Pricing data from homepage + docs + web_search — not verified against live pricing page. |
| **grafiti.ai** | Does not resolve. No acquisition — "Graphiti" is the homegrown engine, not an acquired product. User's assumption was incorrect. |
| **Public revenue** | No public revenue data. ~$1M ARR is a Tracxn estimate, not confirmed. |
| **Domain authority / traffic** | Not in public indices. Requires paid tools for DA/DR and traffic estimates. |
| **Review sites** | No presence on G2, Capterra, or ProductHunt. Enterprise infra tools rarely get public reviews. |
| **Community forum** | No Discord, Slack, or forum found. Community Edition discontinued with no replacement community channel. GitHub is the only community surface. |
| **S&P report** | Full report behind paywall. Only the snippet from Zep's homepage is available. |
| **Context Graph Engine internals** | Proprietary black box. Only Graphiti (open-source engine) reveals the architecture. How Zep scales Graphiti to 100M graphs is undisclosed. |
| **Benchmark methodology** | LoCoMo 94.7% and LongMemEval 90.2% claimed but methodology and full results not independently verified. |

### 5e — Optimization Function

```
maximize: enterprise_revenue × developer_adoption
subject to:
  - graph_native_architecture (no shortcut — temporal KG needs real graph traversal)
  - enterprise_compliance (SOC 2, HIPAA, ABAC — non-negotiable for target market)
  - open_source_funnel (Graphiti must stay Apache-2.0 to attract contributors + users)
  - per_tenant_isolation (enterprise customers demand hard boundaries)
  - sub_200ms_p95_latency (agent memory must not slow down the agent)
  - predictable_pricing (enterprise buyers budget per volume, not per query complexity)
  - small_team (5 people — can't build everything; open-source community fills gaps)
```

| Design choice | Serves which constraint |
|---------------|------------------------|
| Neo4j / FalkorDB (not Postgres) | graph_native_architecture + sub_200ms_p95_latency |
| Graphiti as Apache-2.0 OSS | open_source_funnel + small_team (community contributes) |
| ABAC at substrate | enterprise_compliance + per_tenant_isolation |
| Credit-based pricing (per byte, not per query) | predictable_pricing |
| Bi-temporal validity | graph_native_architecture (temporal is the differentiator) |
| Multi-backend support (Neo4j, FalkorDB, Neptune) | enterprise_revenue (BYO infrastructure) |
| Python-first | open_source_funnel (AI/ML ecosystem) |
| Community Edition discontinued | small_team (focus engineering on cloud revenue) |
| SOC 2 + HIPAA from day one | enterprise_compliance (sell to regulated industries immediately) |

**The product strategy insight:** Zep's endgame is **agent memory as managed infrastructure** — the same way AWS RDS is managed databases. Developers don't run Postgres themselves; they pay AWS to run it. Zep wants developers to not run their own memory layer; they pay Zep. Every architectural choice serves this: graph-native for performance, open-source for trust, compliance for enterprise, predictable pricing for budgeting.

**This is the opposite of Honcho's strategy.** Honcho optimizes for "zero new infrastructure" (Postgres, what devs already have). Zep optimizes for "best-in-class infrastructure, managed for you." Both are valid. Different customers.

### 5f — Copy vs Differentiate

#### What to copy from Zep

| Pattern | Why |
|---------|-----|
| **Bi-temporal validity** (`valid_from`/`valid_to` on every fact) | Table stakes for temporal reasoning. Our epistemic graph needs this — claims have a "when was this true" dimension. ADR-004 §3 already commits to this. |
| **Provenance preserving** (every fact → source) | Every epistemic claim must link to its evidence source. Zep's implementation pattern (source_ids + trace_to_episode) is the right approach. |
| **Hybrid search** (vector + full-text + graph) | Our epistemic graph queries are multi-modal. RRF (Reciprocal Rank Fusion) like Zep/Honcho use is the standard approach. |
| **ABAC at the data layer** (not app middleware) | Team-level access to claims with different confidence needs enforcement at the query level, not the API level. |
| **Open-source engine + internal governance** | Graphiti pattern: core engine is transparent, governance is proprietary. Our epistemic graph: Stream C (Graphiti extraction) is transparent, Stream D (epistemic) is our governance layer. |
| **Observations engine** (pattern mining on graph structure) | "Claims about X have been contradicted 3 times in 6 months" is valuable signal. This is a natural extension of our epistemic graph. |

#### What to differentiate from Zep

| Pattern | Why |
|---------|-----|
| **Graph isolation (per-tenant)** | Zep isolates graphs per user/org. Our epistemic graph needs **shared claims across agents** — different agents contribute to the SAME claim with different confidence. Zep's model is too rigid. |
| **No belief propagation** | Zep tracks facts but doesn't propagate confidence changes. When evidence changes, dependent claims should auto-update. This is our core differentiator — we build it; they don't have it. |
| **No quantitative confidence** | Zep has no confidence scores. Our epistemic graph needs 0.0-1.0 with auto-update on new evidence and decay on stale evidence. |
| **Credit-based pricing** | We're internal infrastructure, not a SaaS product. No pricing model needed. But Zep's credit model shows what the market WILL pay — useful reference for build-vs-buy decisions. |
| **Managed cloud model** | We self-operate. Zep's managed model means they can charge for governance. We get governance "for free" because we're the operator. |
| **Python-first architecture** | Zep is 64% Python. We're TypeScript/Node.js. Different ecosystems, different trade-offs. Don't cargo-cult their language choice. |
| **Single-vendor graph DB** | Zep supports multiple backends for enterprise flexibility. We've chosen FalkorDB (ADR-004). Multi-backend support is operational overhead we don't need. |

---

*Last updated: 2026-07-06*

## 6. Hindsight (Vectorize) — Architecture Deep-Dive

> Phase 6 — conditional. Hindsight uses the same PostgreSQL+pgvector infra we do but adds structured memory extraction (LLM at write time) — the key architectural trade-off vs our verbatim approach.

### 6a — Process/Component Map

```
┌──────────────────────────────────────────────────────────────────┐
│                    1. INGEST (Retain)                             │
│  Chat, docs, events → structured memory extraction via LLM        │
│  $10.00/M tokens (most expensive — LLM at write time)             │
│  Python/TS/Go SDKs, REST API, MCP Server                          │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│              2. STORAGE (PostgreSQL 14+ + pgvector)               │
│  World Facts, Experience Facts, Observations, Mental Models       │
│  pgvector/pgvectorscale/vchord/scann                              │
│  NO graph DB — graph traversal is query-time (TEMPR)              │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│               3. RETRIEVAL (TEMPR Engine)                         │
│  4 parallel: Dense Vector + Sparse/BM25 + Graph + Temporal        │
│  RRF fusion + cross-encoder reranker. $0.75/M tokens.             │
│  Token-budget optimization (not top-K)                            │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                 4. REFLECTION (Reflect)                            │
│  AI reasons over memories → builds beliefs, consolidates obs      │
│  Auto-dedup, evidence tracking, freshness awareness               │
│  $3.00/M tokens                                                   │
└──────────────────────────────────────────────────────────────────┘
```

| Component | What | LLM cost | Trigger |
|-----------|------|----------|---------|
| **Retain** | Structured memory extraction from raw input | $10.00/M (highest) | API call (sync) |
| **Storage** | PostgreSQL + pgvector. 4 memory types. Query-time graph. | $0 | Continuous |
| **TEMPR** | 4-way hybrid: semantic, keyword, graph, temporal | $0.75/M | API call (sync) |
| **Reflect** | AI reasoning over memories → Mental Models | $3.00/M | On-demand/scheduled |

### 6b — Design Choice Tables

| Decision | Choice | Rationale | What they optimize for |
|----------|--------|-----------|----------------------|
| **Primary DB** | PostgreSQL + pgvector (NOT graph DB) | Zero new infrastructure — same rationale as Honcho. Graph traversal is query-time TEMPR, not persisted edges. | Developer adoption over graph-native performance |
| **Memory extraction** | LLM at write time ($10/M Retain) | Structured memories are more useful than raw text. LLM classifies facts, extracts entities, builds observations AT INGEST. | Memory quality over write cost |
| **4 memory types** | World Facts, Experience Facts, Observations, Mental Models | Write-time taxonomy enables targeted retrieval. "Show me World Facts about Alice" vs "Show me Experience Facts." | Structured retrieval precision |
| **TEMPR engine** | 4 parallel strategies + RRF + reranker | No single strategy works for all queries. Semantic for paraphrasing, BM25 for names, graph for relationships, temporal for time. | Recall quality |
| **MIT license** | Fully open source, no feature walls, no telemetry | Same codebase for OSS and Cloud. Builds trust + community. Cloud = managed infra, not more features. | Developer trust + adoption velocity |
| **Observation consolidation** | Auto-dedup + evidence tracking + continuous refinement + freshness awareness | Observations drift. Stale observations re-verified before use. Update, don't overwrite. | Temporal accuracy + evidence trust |
| **Integration breadth** | 40+ agent frameworks | "Be the default memory for every agent." Not competing — integrating. | Ecosystem lock-in |
| **Cloud pricing** | Pay-as-you-go per M tokens. Retain ($10/M) = ~13× Recall ($0.75/M). | Revenue scales with usage. Expensive writes → users self-regulate quality. Cheap reads → frequent retrieval. | Revenue per active user + cost predictability |

### 6c — Hidden Architecture

#### The uncomfortable truth

Hindsight says "agent memory that learns." But the real architecture is in the COST MODEL: **Retain is $10/M — ~13× more expensive than Recall ($0.75/M).** This isn't a bug; it's the entire product strategy.

**1. The LLM IS the memory system.**
Hindsight doesn't just store text. The Retain operation ($10/M) uses an LLM to extract entities, classify facts, build structured observations, detect contradictions, and update stale memories — ALL at write time. This is NOT "vector search over chat logs." Expensive writes ($10/M) produce high-quality structured memories that enable cheap, precise reads ($0.75/M). The asymmetry IS the architecture.

**2. Graph traversal is query-time, not storage-time.**
No graph DB. TEMPR's "Graph Traversal" constructs a graph at query time from relational data: entities → nodes, extracted relations → edges. Same pattern as Honcho's "LLM as graph engine" — but Hindsight does extraction at write time (LLM classifies) + traversal at query time (TEMPR traverses). Honcho does both at query time (LLM tool calls).

**3. The 4 memory types are a retrieval taxonomy, not a storage schema.**
No 4 separate tables. The LLM applies classification labels at write time. "This is a World Fact." "This is an Experience Fact." The taxonomy exists to enable targeted retrieval — the agent queries by memory type. It's a query optimization, not a storage architecture.

**4. Observations are continuous, not batch.**
Zep/Honcho have "Dreamer" processes that run on schedules. Hindsight's observation consolidation is baked into the write pipeline — every new fact checks against existing observations. Auto-dedup, freshness awareness, continuous refinement. More sophisticated than batch dreaming, but contributes to the $10/M write cost.

#### Why this works for Hindsight

| Condition | Hindsight's reality |
|-----------|---------------------|
| **Write volume** | Low — $10/M incentivizes sparse, high-quality writes |
| **Read volume** | High — $0.75/M enables frequent retrieval |
| **Query complexity** | High — 4-way hybrid requires structured memories (LLM-at-write) |
| **Latency** | Asymmetric: writes slow (LLM), reads fast (TEMPR sub-200ms) |
| **Consistency** | Strong — continuous refinement at write time |
| **Revenue** | Pay-as-you-go — expensive writes are the primary revenue driver |

### 6d — Gap Consolidation

| Gap | Detail |
|-----|--------|
| **Pricing page** | HTTP 404. All token costs from web_search — unverified. |
| **Main website** | `vectorize.io` fetch failed. Company data from Perplexity + docs. |
| **Revenue** | No public data. Seed-stage, likely pre-revenue. |
| **Customers** | Only Groq (unverified single-source). No enterprise logos. |
| **Team size** | "2-10" — estimated, not confirmed. |
| **Traffic** | ~48K visits/3mo — single-source estimate. |
| **Benchmarks** | Methodology not independently reproduced. |
| **Cloud/OSS divergence** | Currently same codebase — risk of future divergence. |
| **Bus factor** | Two founders = core team. |

### 6e — Optimization Function

```
maximize: developer_adoption × memory_quality
subject to:
  - zero_new_infrastructure (PostgreSQL, what devs already have)
  - structured_memory_quality (LLM-at-write = expensive but high-quality)
  - cheap_retrieval (reads must be cheap — agents query constantly)
  - integration_breadth (be the default memory for every agent framework)
  - MIT_open_source (no feature walls, same codebase)
  - self_serve_onboarding (Docker one-command, 60-second quick start)
  - small_team (2-10 people — ecosystem fills gaps)
```

| Design choice | Serves constraint |
|---------------|-------------------|
| PostgreSQL + pgvector | zero_new_infrastructure |
| LLM-at-write ($10/M Retain) | structured_memory_quality |
| TEMPR 4-way ($0.75/M Recall) | cheap_retrieval |
| 40+ integrations | integration_breadth |
| MIT, same codebase | MIT_open_source |
| Docker one-command | self_serve_onboarding |
| Observation consolidation at write | structured_memory_quality |
| 4 memory types taxonomy | cheap_retrieval (targeted queries = fewer tokens) |

**Strategy insight:** Hindsight's endgame is **memory as a utility** — every agent should use it, like every app uses a database. The asymmetric cost model is brilliant: expensive writes incentivize quality; cheap reads enable ubiquity. **This is the opposite of our verbatim approach** — we trade write structure for zero write cost.

### 6f — Copy vs Differentiate

#### What to copy

| Pattern | Why |
|---------|-----|
| **4 memory types taxonomy** | Clear typing for claims: raw facts, event-attributed, consolidated patterns, curated beliefs |
| **Observation consolidation** | Auto-dedup + evidence tracking + freshness — our epistemic graph needs this |
| **TEMPR retrieval** | 4 parallel strategies + RRF — proven pattern for multi-modal memory queries |
| **Memory bank config** | Mission, Directives, Disposition — agents need configuration at the memory layer |
| **Integration strategy** | "Default for every agent" — our epistemic graph should integrate with our agent workflows |

#### What to differentiate

| Pattern | Why |
|---------|-----|
| **LLM-at-write extraction** | $10/M is expensive. Our verbatim approach has zero write cost. Different trade-off. |
| **PostgreSQL-only (no graph DB)** | We chose FalkorDB (ADR-004) for belief propagation. Query-time graph is limited vs persistent edges. |
| **No belief propagation** | Our core differentiator. Confidence changes must propagate through dependent claims. |
| **No quantitative confidence** | "Proof count" ≠ confidence. Our 0.0-1.0 model with source-weighted aggregation is unique. |
| **Agent memory vs org epistemology** | Hindsight = individual agents. We need TEAMS of agents building shared knowledge. Different data model. |
| **No multi-agent claim aggregation** | Hindsight's memory bank is per-agent. Our epistemic graph needs shared claims — fundamentally different. |

---

*Last updated: 2026-07-06*

---

## 7. Confidence Scoring Landscape — Taxonomy & Patterns

> Research across 10 systems that implement confidence scoring on knowledge claims. Key patterns for epistemic graph design.

### 7a — How Clarity Does It

Precision-weighted Bayesian updating under the Free Energy Principle. Three mechanisms: (1) confidence = posterior precision, learning rate scales inversely with confidence, (2) observation count as Beta-prior evidence mass, (3) time-decay on unreinforced beliefs. Contradictions drop confidence before changing belief content. Every belief traceable to source observations.

**Implication for us:** Clarity has the most production-ready API design for belief+confidence. Their observation-count + precision-weighting + time-decay pattern is directly applicable to our epistemic graph. We should adopt: traceable provenance per claim, observation count as evidence mass, time-decayed staleness.

### 7b — Taxonomy of Approaches

| Category | Systems | Mechanism | Propagation? | Calibrated? |
|----------|---------|-----------|-------------|-------------|
| **Bayesian / Precision-Weighted** | Clarity, HGF | Posterior precision; precision-weighted updates | Local only | Yes |
| **Multi-Module Voting + Integration** | NELL (CMU) | Modules propose with confidences; Knowledge Integrator resolves | **Yes** — KI propagates | ~87% precision |
| **Factor Graph / Joint Probabilistic** | DeepDive (Stanford) | Gibbs sampling on factor graph → marginal probabilities | **Yes** — via factor graph structure | Yes (calibrated) |
| **Multi-Layer Probabilistic Fusion** | Google Knowledge Vault, Diffbot | Source trust × corroboration × consistency | Limited | Probabilistic |
| **Source-Weighted Max** | YAGO | confidence = max(accuracy × trust) across witnesses | No | Empirically verified |
| **Graph-Theoretic Contractive Propagation** | Nikooroo & Engel (2024) | Credibility (static) + confidence (emergent, propagated) → fixed point | **Yes** — core mechanism | By contractivity guarantee |
| **Dynamic Recalculation on Read** | Recall, SSGM | effective = stated × (support − challenge) with saturation curves | **Yes** — contradiction cascades | Not inherently |
| **Subjective Logic / Opinion** | Jøsang's SL | (b, d, u, a) opinion with explicit uncertainty | **Yes** — transitivity + fusion | Depends on base rates |
| **LLM Self-Assessment** | Various | Model self-reports; token-level probability | No | Often poorly calibrated |
| **Evidence-Based PageRank** | IBM Watson | Contextual PageRank + provenance reliability | PageRank-level | Bucketed (coarse) |

### 7c — Key Patterns for Our Epistemic Graph

| Pattern | Description | Systems using it | Should we adopt? |
|---------|-------------|-----------------|-----------------|
| **Decouple credibility from confidence** | Credibility = source-driven, static. Confidence = structure-driven, emergent, propagated. Nikooroo & Engel formalize this cleanly. | Nikooroo & Engel, Clarity (implicitly) | **Yes** — this is the most important pattern. Our confidence (0.0-1.0) should emerge from graph structure, not just source trust. |
| **Dynamic recalculation** | Confidence is never a stored field. Recalculated on every read or graph mutation. | Recall, SSGM, NELL, Nikooroo & Engel | **Yes** — static confidence is stale the moment new evidence enters the graph. |
| **Propagation through typed, weighted edges** | Support edges (+weight) and contradiction edges (−weight). Normalize by outgoing edge mass. Guarantee convergence to fixed point. | Nikooroo & Engel, Recall | **Yes** — our supports/contradicts edges should have weights that propagate. Contractive propagation guarantees convergence. |
| **Saturation curves** | Prevent runaway scores: `support_effect = 0.9 × tanh(support_mass)`. A thousand weak supports shouldn't overwhelm one strong contradiction. | Recall, Nikooroo & Engel | **Yes** — simple, proven, prevents confidence inflation. |
| **Explicit uncertainty (not just low confidence)** | Subjective Logic: belief + disbelief + uncertainty = 1. 0.9 confidence = could be strong evidence + slight doubt, OR moderate evidence + lots unknown. | Jøsang's SL | **Maybe** — adds complexity. Start with simple 0-1 confidence, add uncertainty dimension later if needed. |
| **Observation count as evidence weight** | Track number of observations supporting each claim. Beta-prior update for binary beliefs. Time-decayed for staleness. | Clarity | **Yes** — simple, interpretable, maps to Bayesian updating. |
| **Provenance is non-negotiable** | Every belief/claim links to its evidence sources. Without provenance, confidence is a magic number. | Clarity, Diffbot, YAGO, IBM Watson, SSGM | **Yes** — already in our design (ADR-004). |
| **Contradiction ≠ averaging** | Conflicting evidence should NOT be averaged. Drop confidence. Repeated contradictions change the belief. Logical contradiction check rejects incompatible updates. | Clarity, Subjective Logic, SSGM | **Yes** — our epistemic graph needs contradiction handling that preserves information, not averages it away. |

### 7d — Recommended Implementation Priority

1. **Claim → source provenance** (table stakes — already in ADR-004)
2. **Observation count per claim** (simple, from Clarity — stores how many times a claim was observed/asserted)
3. **0.0-1.0 confidence with time-decay** (simple — confidence decays if claim not re-verified in N days)
4. **Support/contradict edges with weights** (from Nikooroo & Engel — enables graph propagation)
5. **Contractive propagation to fixed point** (from Nikooroo & Engel — mathematically sound, guarantees convergence)
6. **Saturation curves on edge weights** (from Recall — prevents runaway inflation)
7. **Explicit uncertainty dimension** (from Subjective Logic — defer; add if simple confidence proves insufficient)

---

*Last updated: 2026-07-06*

---

## 8. Confidence Patterns — Applicability Analysis

> Deep-dive: which confidence scoring patterns apply to our epistemic graph, which don't, and why. Framed around our core approach: **when sources conflict, extract the mechanical nuances that make each position true in its context, then reframe the question to "what's true for our case given these mechanics?"** — rather than trying to create a single universal truth.

### The Core Shift: From Truth-Resolution to Mechanics-Decomposition

Most confidence scoring systems (NELL, Google Knowledge Vault, YAGO) operate on a single axis: **"how likely is this claim to be true?"** They resolve contradictions by picking the highest-confidence version or averaging. This works for simple factual claims ("Paris is the capital of France" vs "Lyon is the capital of France" — pick the one with more consensus).

Our domain is different. We deal with claims like "organic outperforms paid acquisition" where both sides have evidence. The question isn't "which is true?" but **"under what mechanics is each true, and which mechanics apply to our specific context?"**

This reframes confidence from a single number to a **three-dimensional structure:**

```
Traditional:  Claim → [0.0–1.0 confidence]
Our approach:  Claim → {boundary conditions, mechanical model, source, context}
                       → "True when X holds, because of mechanism Y, per source Z"
                       → Confidence in claim = confidence in {boundary match + mechanical soundness}
```

### Pattern-by-Pattern Analysis

---

#### Pattern 1: Decouple Credibility from Confidence

**What it normally means:** Credibility = how trustworthy the source is (static, a priori). Confidence = how supported the claim is by graph structure (emergent, propagated). Nikooroo & Engel formalize this as credibility(Ψ) vs confidence(Φ).

**Applies to us?** **Yes, with a critical modification.**

The standard model treats credibility as a single scalar: "Source A is 0.8 reliable." This is too coarse for our domain. A source might be highly credible on one mechanical pathway and clueless on another. A VC who nailed marketplace predictions might be terrible at predicting SaaS churn dynamics.

**Our modification — multi-dimensional credibility:**

```
Traditional:  credibility(source) = 0.8
Our approach:  credibility(source, mechanical_domain) = {
                 "marketplace dynamics": 0.9,
                 "consumer psychology": 0.7,
                 "enterprise sales": 0.3
               }
```

A source's credibility is not a property of the source — it's a property of the **intersection** between the source and the mechanical domain of the claim. This means:

- **Don't ask:** "How reliable is this source?" (impossible to answer generally)
- **Ask:** "How reliable is this source about THIS SPECIFIC MECHANICAL PATHWAY?" (answerable from track record)

**What we should build:** Credibility is stored as a **per-domain vector**, not a scalar. When a claim enters the graph, its initial confidence is influenced by the source's credibility in the claim's mechanical domain. This initial influence then gets modified by structural propagation (Pattern 3).

**What we should NOT do:** Assign a single credibility score to a source. That collapses valuable mechanical information and produces the very "single universal truth" problem we're trying to avoid.

---

#### Pattern 2: Dynamic Recalculation

**What it normally means:** Confidence is never a stored field. It's recalculated on every read or graph mutation. Recall's formula: `effective = stated × (support − challenge)` with saturation curves.

**Applies to us?** **Yes, and it's even more important for our approach.**

In a traditional system, dynamic recalculation handles simple drift: "Alice changed jobs, so 'Alice works at Acme' is now false." In our system, recalculation handles a more complex case: **boundary condition shifts.**

Example: Claim "organic outperforms paid" has confidence 0.85. Then a new source provides evidence that "organic outperforms paid WHEN customer LTV > $200, but paid outperforms organic WHEN customer LTV < $50." The claim isn't falsified — it's **qualified.** The boundary conditions tighten, and confidence in any specific application now depends on whether the boundary conditions match.

**What we should build:** Recalculation that fires not just on new evidence, but on new **mechanical insights** — when a claim's boundary conditions are refined, all dependent claims that assumed looser boundaries should recalculate.

**What we should NOT do:** Recalculate confidence as a simple weighted average of all evidence. This is the averaging trap. When 5 sources say "organic wins" and 2 say "paid wins," averaging produces 0.7 confidence in organic — but if the 2 saying "paid wins" are right about a specific mechanical pathway (low-LTV customers), averaging destroys that signal. Instead, the system should **branch the claim** into contextualized variants with their own confidence.

---

#### Pattern 3: Propagation Through Typed, Weighted Edges

**What it normally means:** Support edges (+weight) and contradiction edges (−weight). Normalize by outgoing edge mass. Guarantee convergence to fixed point. Nikooroo & Engel's contractive propagation model.

**Applies to us?** **Yes, but we need richer edge types.**

Standard edge types (support/contradict) are too binary for mechanical reasoning. When a source claims "paid acquisition is better" and another claims "organic is better," the relationship isn't simple contradiction — it's **different mechanical pathways producing different outcomes under different conditions.**

**Our needed edge types:**

| Edge type | Meaning | Example |
|-----------|---------|---------|
| **supports** | Evidence A directly reinforces claim B | "Experiment X showed +30% organic lift" supports "organic outperforms paid in our segment" |
| **contradicts** | Evidence A directly opposes claim B | "Enterprise data shows paid 2× more efficient" contradicts "organic always wins" |
| **qualifies** | Evidence A narrows the boundary conditions of claim B | "Organic wins for LTV > $200" qualifies "organic outperforms paid" — it's true, but only under that condition |
| **reveals_mechanics_of** | Evidence A explains WHY claim B is true/false | "Organic outperforms because repeat purchase rate is 3× higher" reveals the causal mechanism |
| **is_special_case_of** | Claim A is claim B under specific conditions | "'Paid wins for LTV < $50' is a special case of the general acquisition efficiency question" |
| **depends_on** | Claim A's truth depends on claim B being true | "Organic's efficiency advantage depends on content production being cheap" |

**Propagation semantics under our approach:**

Traditional propagation: support edges increase confidence linearly. Our propagation should be **mechanically aware:**

- A `qualifies` edge doesn't decrease confidence — it **distributes** it across narrower contexts. "Organic outperforms paid" (confidence 0.7 globally) → after qualification, becomes: "Organic outperforms paid for LTV > $200" (confidence 0.9) AND "unclear for LTV < $200" (confidence 0.3, needs more evidence).
- A `reveals_mechanics_of` edge increases confidence in the claim it explains AND increases credibility in the source that provided the mechanics (since demonstrating causal understanding is stronger than asserting outcomes).
- A `contradicts` edge triggers a **decomposition** process: the system attempts to find a `qualifies` edge that resolves the contradiction by identifying different boundary conditions. Only if no qualifying edge exists after investigation does the contradiction reduce confidence.

**What we should build:** These 6 edge types with mechanical-aware propagation semantics. The propagation should prioritize **decomposition** (finding qualifiers) over **resolution** (picking a winner).

**What we should NOT do:** Simple support/contradict binary edges that collapse mechanical nuance into a single confidence number.

---

#### Pattern 4: Observation Count as Evidence Weight

**What it normally means:** Count how many times a claim was observed/asserted. Use as Beta-prior evidence mass. More observations = higher confidence in a simple Bayesian update. Clarity's model: "Prefers concise" starts at 0.4, climbs to 0.85 after 14 consistent observations.

**Applies to us?** **Partially. The count matters, but the diversity of mechanical pathways matters more.**

Ten observations from the same source saying "organic wins because content is cheap" are not 10 independent pieces of evidence — they're one mechanical pathway repeated 10 times. One observation from a source explaining "organic wins because retention compounds across cohorts" is MORE valuable than the 10 repetitions, because it reveals a NEW mechanical pathway.

**Our modification — mechanical diversity weighting:**

```
Traditional:  confidence ∝ observation_count
Our approach:  confidence ∝ mechanical_pathways_observed
               where each pathway is a distinct causal mechanism, not a distinct observation
```

Two observations that reveal the same mechanism count as 1 pathway. Two observations that reveal different mechanisms count as 2 pathways. This prevents the system from being gamed by repetition and rewards genuine mechanical exploration.

**What we should build:** Observation tracking that groups observations by their underlying mechanical claim. When a new observation arrives, check: "is this the same mechanical pathway we've already seen, or a new one?" Weight confidence by pathway diversity, not raw count.

**What we should NOT do:** Raw observation counting that treats every assertion as independent evidence. This is the clickbait problem — 100 articles citing the same flawed study are not 100 independent confirmations.

---

#### Pattern 5: Provenance is Non-Negotiable

**What it normally means:** Every claim links to its evidence source. Without provenance, confidence is unverifiable. All systems agree on this.

**Applies to us?** **Yes, with an extension — provenance must include the mechanical model, not just the source.**

Standard provenance: "Claim X per Source A (URL, date)." This tells us WHO said it and WHEN, but not WHY they believe it. For our approach, the WHY is the valuable part.

**Our extended provenance:**

```
Claim: "Organic outperforms paid for customer acquisition"
├── Evidence 1:
│   ├── Source: Internal experiment (2025-Q3, n=1,200)
│   ├── Mechanical model: "Organic visitors have 3× higher repeat purchase rate,
│   │   compounding LTV over 6-month horizon"
│   ├── Boundary conditions: "Applies to customers with LTV > $200,
│   │   in categories with high repeat purchase rates"
│   └── Credibility in this mechanical domain: 0.8 (internal data, high sample)
├── Evidence 2:
│   ├── Source: Industry report (McKinsey 2024)
│   ├── Mechanical model: "Organic CAC is 60% lower than paid after brand awareness
│   │   reaches 40% threshold — network effects reduce marginal acquisition cost"
│   ├── Boundary conditions: "Applies to markets where brand awareness is measurable
│   │   and network effects exist"
│   └── Credibility in this mechanical domain: 0.6 (consulting report, broad methodology)
```

**Why this matters:** When evidence 1 and evidence 2 conflict with a third source, we can identify EXACTLY where the disagreement lies — not "these sources disagree" but "source 3 disputes the assumption that repeat purchase rates compound linearly." This enables the decomposition approach: instead of averaging confidence, investigate the linearity assumption.

**What we should build:** Provenance that captures: (1) source, (2) date, (3) mechanical model relied upon, (4) boundary conditions assumed, (5) credibility in the specific mechanical domain.

**What we should NOT do:** Thin provenance (source + date only) that strips out the mechanical reasoning. This reduces every claim to "someone said X" and makes mechanical decomposition impossible.

---

#### Pattern 6: Contradiction ≠ Averaging

**What it normally means:** When sources disagree, don't average their positions. Drop confidence in both. Repeated contradictions change beliefs. Logical contradiction check rejects incompatible updates. Clarity: drops confidence before changing belief content.

**Applies to us?** **This IS our approach — and we should formalize it as a first-class operation.**

The standard pattern says "contradiction → drop confidence → if persistent, change belief." This is reactive — it treats contradiction as a problem to resolve. Our approach treats contradiction as a **signal to decompose.**

**Our contradiction protocol:**

```
1. Detect contradiction: Source A claims X, Source B claims not-X
2. Pause resolution — do NOT drop confidence yet
3. Decompose: Extract the mechanical model from each source
   - Source A believes X because of mechanism M_A
   - Source B believes not-X because of mechanism M_B
4. Identify boundary conditions: Under what conditions is M_A dominant? M_B?
5. Reframe: Create contextualized claims
   - "X is true when [boundary A holds]" — confidence derived from Source A + mechanical soundness of M_A
   - "not-X is true when [boundary B holds]" — confidence derived from Source B + mechanical soundness of M_B
   - "For our context [boundary C], the applicable claim is [whichever matches]"
6. If boundary conditions cannot be identified → THEN flag as unresolved contradiction, drop confidence in both, trigger deeper investigation
```

**This is fundamentally different from the standard approaches:**

| Step | Standard approach | Our approach |
|------|------------------|--------------|
| Detect contradiction | Error signal | Investigation signal |
| Response | Drop confidence, pick winner, or average | Decompose into mechanical models |
| Output | "Claim X has low confidence" | "Claim X is true under conditions A, Claim not-X is true under conditions B, our context = C" |
| Unresolvable case | Low confidence in both | Low confidence + flagged for deeper investigation |
| Learning | System learns which sources are more reliable | System learns which MECHANICS are dominant under which CONDITIONS |

**What we should build:** First-class `decompose_contradiction` operation that extracts mechanical models, identifies boundary conditions, and produces contextualized claims. This is the core differentiator of our approach.

**What we should NOT do:** The standard contradiction resolution pipeline (average, vote, or suppress the minority view). That destroys the mechanical signal that contradictions carry.

---

### Summary: What Applies, What Doesn't, and What We Modify

| Pattern | Applies? | Modification for our approach |
|---------|----------|------------------------------|
| **Decouple credibility from confidence** | ✅ Yes | Multi-dimensional credibility (per mechanical domain), not scalar per source |
| **Dynamic recalculation** | ✅ Yes | Fire on new mechanical insights and boundary condition refinement, not just new evidence |
| **Typed weighted edges** | ✅ Yes | 6 edge types (support, contradict, qualify, reveal_mechanics, special_case, depends_on) with mechanical-aware propagation |
| **Observation count** | ⚠️ Modified | Mechanical pathway diversity > raw count. Group observations by underlying mechanism. |
| **Provenance** | ✅ Yes | Extended: source + date + mechanical model + boundary conditions + domain-specific credibility |
| **Contradiction ≠ averaging** | ✅ **Core** | First-class decomposition protocol. Contradiction = signal to find boundary conditions, not error to resolve. |
| **Saturation curves** | ✅ Yes | Adopt as-is — prevents runaway inflation. `tanh()` on edge weights. |
| **Explicit uncertainty** | ⚠️ Defer | Adds complexity. Start with contextualized confidence; add uncertainty dimension when mechanical ambiguity proves too fine-grained for simple confidence. |

### What NOT to Build (Anti-Patterns for Our Approach)

| Anti-pattern | Why it's wrong for us |
|--------------|----------------------|
| **Single scalar confidence per claim** | Collapses mechanical nuance. Can't express "high confidence that X is true under condition A, low confidence under condition B." |
| **Source-level credibility scores** | A source can be highly credible on marketplace dynamics and useless on consumer psychology. Scalar credibility destroys this signal. |
| **Simple support/contradict binary edges** | Forces false dichotomies. Most real disagreements are about boundary conditions, not facts. Need `qualifies` and `reveals_mechanics` edges. |
| **Averaging contradictory evidence** | Destroys the signal that contradictions carry. The fact that sources disagree about specific mechanics IS the valuable information. |
| **Confidence-as-voting** | More sources saying X doesn't make X true. It makes X popular. Our epistemic graph needs mechanical truth, not popularity. |
| **Static stored confidence** | Confidence is contextual — it depends on which boundary conditions apply. Storing "0.7" without context makes it meaningless outside the context it was computed for. |

---

### Implementation Priority (Revised)

Given our approach of mechanical decomposition over truth-resolution:

1. **Extended provenance** (mechanical model + boundary conditions) — foundation. Without this, nothing else works.
2. **6 edge types with mechanical-aware semantics** — enables decomposition, qualification, and contextualization.
3. **Contradiction decomposition protocol** — the core operation. Turns contradiction from error into insight.
4. **Multi-dimensional credibility** — enables nuanced source trust without collapsing mechanical domains.
5. **Mechanical pathway diversity weighting** — replaces naive observation counting.
6. **Dynamic recalculation on boundary condition refinement** — ensures confidence stays contextual.
7. **Saturation curves** — simple safety mechanism, adopt as-is when edge weights are implemented.

---

*Last updated: 2026-07-06*

---

## 9. How This Modifies Connor's Carroll Mechanisms

> Mapping our 6 confidence patterns against Connor's specific mechanisms. What changes, what stays, and why.

### Connor's Approach — Quick Reference

| Mechanism | What it does | How confidence flows |
|-----------|-------------|---------------------|
| **Points + LMSR markets** | Every proposition has a prediction market. Price = aggregate belief. | Market-driven. Money moves prices. |
| **Operators-as-points** | Relevance R(A,B) is itself a tradeable proposition. NAND constraint: ¬(A∧B∧R). | Relevance is adversarial — buy/sell R to assert/deny connection. |
| **Signal ≠ price** | Resolution reads signal (computed from positions), not price. | Uncoupled from capturable market prices. |
| **Liveness** `w = p_B · p_R` | Is this test alive? Fast, price-based, rentable. | Flow-based. Can be pumped with money. |
| **Grounding** `g = (I−λM)⁻¹a` | Is this point connected to resolution events? Slow, propagated, unrentable. | PageRank over relevance edges. Anti-collusion gate. |
| **Doubt** | Adverse-trial contracts. κ-detection. Slash-to-B. | Adversarial — someone profits from proving you wrong. |
| **Track records** | Per-position ledger of adverse trials survived. h-weighted. | Person-independent, non-transferable. |
| **Schedule** `f = √(a·r)·p_B` | Cobb-Douglas voice gate. | Market-weighted, voice-gated. |

### Modifications — Pattern by Pattern

---

#### Modification 1: Multi-Dimensional Credibility → Modifies Liveness

**Connor's liveness:** `w = p_B · p_R` — purely price-based. A counterpoint B with high market price and high relevance price has high liveness, regardless of whether the source behind B is mechanically credible on this specific domain.

**The problem:** A well-funded but mechanically incoherent counterpoint can achieve high liveness. If a VC with deep pockets buys B and R, the test is "live" — but the mechanical model behind B might be nonsense. Liveness conflates "someone believes this" with "this is worth testing."

**Our modification:**

```
Connor:    w = p_B · p_R
Modified:  w = p_B · p_R · credibility(B, mechanical_domain)
```

Where `credibility(B, domain)` is NOT a scalar per source, but a vector per (source, mechanical domain) pair. A source's credibility on "marketplace dynamics" doesn't transfer to "consumer psychology." The credibility term gates liveness: a test is only alive if the counterpoint comes from a source with demonstrated credibility in the relevant mechanical domain.

**Concrete example:**
- Counterpoint B: "Paid outperforms organic" — backed by a VC known for marketplace expertise (credibility = 0.9 in "marketplace dynamics")
- Counterpoint B': "Paid outperforms organic" — backed by an anonymous blog (credibility = 0.1 in "marketplace dynamics")
- Under Connor's model: both have the same liveness if p_B and p_R are equal
- Under our modification: B has 9× the liveness of B' — the VC-backed claim is genuinely more worth testing

**What stays:** Connor's insight that liveness gates voice magnitude, bleed rate, and slash intensity. Our modification only adjusts the liveness computation; the downstream mechanics remain intact.

---

#### Modification 2: Mechanical Decomposition → Modifies the NAND Constraint

**Connor's NAND:** ¬(A∧B∧R) — "you cannot have A true, B true, and R true simultaneously." If B is true AND R is true, A must be false. This forces binary opposition.

**The problem:** Most real disagreements aren't binary. "Organic outperforms paid" and "Paid outperforms organic" aren't logically incompatible — they're true under different boundary conditions. Connor's NAND would force one to be false, destroying the mechanical signal in the contradiction.

**Our modification — pre-NAND decomposition step:**

```
Before NAND is applied:

1. Detect: A = "Organic wins," B = "Paid wins," R = "B is relevant to A"
2. Decompose B into its mechanical model:
   B_mechanics = "Paid wins WHEN customer LTV < $50,
                 because low-LTV customers don't generate repeat purchases
                 that compound organic's advantage"
3. Check: Does B genuinely contradict A, or does it qualify A?
   - B's mechanical model reveals: A is true when LTV > $200
   - B's mechanical model reveals: B is true when LTV < $50
   - These aren't contradictions — they're boundary conditions
4. Reframe:
   - A' = "Organic wins for LTV > $200" (confidence from A's evidence)
   - B' = "Paid wins for LTV < $50" (confidence from B's evidence)
   - R' = "B' is relevant to A' as a boundary condition qualifier"
5. NAND on (A', B', R'): ¬(A'∧B'∧R') — now this makes sense
   - They operate in DIFFERENT LTV ranges → R' is false (no genuine relevance)
   - NAND is satisfied without forcing a false binary
```

**What changes in the operator model:**

| Connor's operators | Our addition |
|--------------------|-------------|
| `R(A,B)` — "B is relevant to A" (tradeable) | Unchanged — relevance is still tradeable |
| (none) | `qualifies(A,B)` — "B reveals boundary conditions for A" (mechanical, not just adversarial) |
| (none) | `reveals_mechanics_of(A,B)` — "B explains the causal mechanism behind A" |

These new edge types don't replace R(A,B) — they augment it. `R(A,B)` still exists for adversarial relevance trading. But the system first attempts mechanical decomposition via `qualifies` and `reveals_mechanics_of` before routing to adversarial NAND.

**What stays:** The NAND constraint is correct and elegant for genuine logical contradictions. Our modification adds a pre-processing step that determines whether the contradiction is genuine or a boundary condition ambiguity. Only genuine contradictions reach NAND.

---

#### Modification 3: Six Edge Types → Modifies Operators

**Connor's operators:** All of type R(A,B) — "B is relevant to A." Single edge type, tradeable, adversarial.

**The problem:** "Relevance" conflates at least five different relationships:
- "B proves A false" (contradiction — should reduce confidence in A)
- "B is evidence FOR A" (support — should increase confidence)
- "B reveals the conditions under which A is true" (qualification — should contextualize A)
- "B explains WHY A is true" (mechanical revelation — should increase mechanical credibility of A)
- "B depends on A being true" (dependency — A's truth is a prerequisite for B)

Connor's model lumps all of these into R(A,B) and lets the market sort it out. But the market can't distinguish between "B contradicts A" and "B qualifies A" — both look like "B is relevant to A" to a trader. This loses the mechanical signal.

**Our modification — typed operators:**

| Edge type | Operator | Market semantics | Confidence effect |
|-----------|----------|------------------|-------------------|
| **Relevance** | R(A,B) | Tradeable. "B bears on A." (Connor's original) | Ambiguous — market decides |
| **Supports** | S(A,B) | Tradeable. "B is evidence FOR A." | Increases confidence in A when B is true |
| **Contradicts** | C(A,B) | Tradeable. "B is evidence AGAINST A." | Decreases confidence in A when B is true. Triggers decomposition protocol. |
| **Qualifies** | Q(A,B) | Tradeable. "B reveals boundary conditions for A." | Contextualizes A — distributes confidence across B's conditions |
| **Reveals mechanics** | M(A,B) | Tradeable. "B explains the causal mechanism behind A." | Increases mechanical credibility of A (different from confidence — see Modification 1) |
| **Depends on** | D(A,B) | Tradeable. "A's truth depends on B being true." | A's confidence bounded by B's confidence |

**Each edge type IS a point** — it has its own LMSR market, its own price, its own holders. This preserves Connor's operator-as-point design. The innovation is that the edge type determines its propagation semantics, not just that it's "relevant."

**What stays:** Operators are still points with markets. The NAND constraint still applies to contradiction edges C(A,B). The insight that relevance is tradeable and adversarial is preserved. We're adding types, not removing the market.

---

#### Modification 4: Mechanical Pathway Diversity → Modifies Grounding

**Connor's grounding:** `g = (I−λM)⁻¹a` — PageRank over relevance edges. `a[i]` = EWMA of resolution events at point i. Resolution events propagate grounding through the relevance matrix M.

**The problem:** Two resolution events on the SAME mechanical pathway provide less grounding than two on DIFFERENT pathways. If three experiments all test "organic wins because repeat purchase rate is higher" (same mechanism), they provide one mechanical pathway's worth of grounding. If three experiments test three different mechanisms, they provide 3× the grounding. Connor's PageRank treats them identically.

**Our modification — pathway-weighted M:**

```
Connor:    M[i][j] = normalized p_R(i,j)  (single relevance weight)
Modified:  M[i][j] = normalized p_R(i,j) × pathway_diversity(i,j)

Where pathway_diversity(i,j) = |{distinct mechanical pathways connecting i to j}|
```

A point connected to 3 resolution events through 3 different mechanical pathways gets 3× the grounding of a point connected to 3 resolution events through the same pathway repeated. This prevents "grounding farming" — running the same experiment 10 times doesn't create 10× grounding.

**Concrete example:**
- Claim: "Organic outperforms paid"
- Resolution event E1: test of "repeat purchase rate" mechanism → grounding contribution: 1 pathway
- Resolution event E2: test of "repeat purchase rate" mechanism AGAIN → grounding contribution: 0 (same pathway, already counted)
- Resolution event E3: test of "brand awareness threshold" mechanism → grounding contribution: 1 NEW pathway
- Total grounding: 2 pathways (not 3 events)

**What stays:** The PageRank propagation structure (I−λM)⁻¹ is elegant and correct. Our modification only changes how M is populated — adding a pathway diversity factor. The mathematical properties (convergence, damping) remain intact.

---

#### Modification 5: Extended Provenance → Modifies Track Records

**Connor's track records:** Per-position ledger of adverse trials survived. h-weighted. Attached to positions, not persons. Measures "did this position survive when evidence went against it?"

**The problem:** A position can survive an adverse trial for two very different reasons:
1. **Mechanical soundness:** The underlying mechanical model was correct, and the trial's apparent contradiction was resolved by identifying boundary conditions
2. **Luck:** The trial happened to use boundary conditions where the position was accidentally correct, but the mechanical model was wrong

Connor's track record treats both identically — "survived an adverse trial." This rewards luck as much as mechanical soundness.

**Our modification — extended trial ledger:**

```
Connor's trial record:
  {trial_id, position, resolution_direction, |LLR|}

Our trial record:
  {trial_id, position, resolution_direction, |LLR|,
   mechanical_model_relied_upon,          ← what mechanics was the position based on?
   boundary_conditions_at_trial_time,     ← what boundary conditions were in play?
   decomposition_result}                  ← did the trial reveal new boundary conditions?
```

This enables:
- **Mechanical survivorship:** Track not just "did the position survive?" but "did the MECHANICAL MODEL survive?" A position that survived because its model was right → high track record. A position that survived because boundary conditions happened to align → lower track record.
- **Boundary condition discovery:** When a trial reveals new boundary conditions (the position is true, but only under narrower conditions than previously thought), this is valuable learning — the track record should reflect that the position WAS correct but IS NOW contextualized.
- **Model deprecation:** When a position's underlying mechanical model is superseded by a better model, the track record of the OLD model should be preserved as historical context but not applied to the NEW model.

**What stays:** The position-ledger design (D25) — ledgers attach to positions, not persons. No veteran premium. No credibility farming across questions. Our modification adds mechanical context to the ledger entries, not person-level tracking.

---

#### Modification 6: Contradiction Decomposition → Modifies Doubt

**Connor's doubt:** Adversarial-trial contracts. Someone buys a doubt contract on position B, putting up collateral. If evidence resolves against the doubted position, the doubter profits (slash-to-B). κ-detection identifies bias by finding the equilibrium where slashing matches evidence movement.

**The problem:** Connor's doubt is purely adversarial — someone must actively bet against a position to trigger a trial. This works for contested claims with active opposition, but misses two important cases:

1. **No one is betting against a claim that SHOULD be tested.** A claim might be mechanically dubious but uncontested because no one has noticed or no one wants to put up collateral. The system passively accepts it.
2. **Adversarial trials collapse mechanical nuance.** When a trial resolves "against" a position, it declares the position false — but the truth might be "true under narrower conditions." The adversarial format rewards binary outcomes and punishes nuance.

**Our modification — collaborative pre-adversarial decomposition:**

```
Before the adversarial doubt layer activates:

1. System detects: Source A claims X, Source B claims not-X
2. System extracts mechanical models from both sources
3. System attempts to identify boundary conditions that make both true
4a. If boundary conditions found → reframe into contextualized claims.
    Adversarial trial is UNNECESSARY. Both positions are correct in their contexts.
    The system learned more from decomposition than from a binary trial.
4b. If boundary conditions CANNOT be found → flag as genuine unresolved contradiction.
    NOW the adversarial layer activates. Someone should bet against one of these.
5. If adversarial trial resolves → feed the resolution back into the decomposition
   engine. The trial didn't just pick a winner — it provided new evidence about
   which boundary conditions apply.
```

**This changes Connor's doubt in two ways:**

| Connor's doubt | Our addition |
|----------------|-------------|
| Adversarial only — someone must actively bet | Collaborative pre-processing — system attempts to resolve before adversarial layer |
| Binary outcomes — position is right or wrong | Contextualized outcomes — position is right under conditions C1, wrong under C2 |
| κ-detection identifies bias in betting patterns | κ-detection ALSO identifies when decomposition should have resolved a contradiction but didn't — suggesting the adversarial layer was used unnecessarily |

**What stays:** The adversarial doubt mechanism is correct for genuinely unresolvable contradictions where no qualifying boundary conditions exist. The slash-to-B mechanism, κ-detection, and adverse-trial contracts remain intact. Our modification adds a pre-processing layer that routes the easy cases (boundary condition ambiguities) to decomposition, reserving adversarial trials for the hard cases (genuine logical contradictions).

---

### What Does NOT Change

| Connor's mechanism | Why it stays |
|--------------------|-------------|
| **Signal ≠ price** (D8, D9) | Correct and essential. Confidence should never be capturable by money. Our modifications add mechanical dimensions to signal computation, but the principle stands. |
| **Points with LMSR markets** | The market layer is valuable for aggregating belief across agents. Our modifications don't replace it — they add a mechanical decomposition layer BETWEEN the market and the confidence output. |
| **Operators-as-points** | Relevance, support, contradiction, qualification — all should be tradeable propositions. Our modification adds types, not removes the market. |
| **Position-ledger design** (D25) | Correct. Track records should attach to positions, not persons. No veteran premium. Our modification adds mechanical context to ledger entries, not person-level tracking. |
| **NAND constraint** | Correct for genuine logical contradictions. Our modification adds a pre-processing step to determine whether a contradiction is genuine or a boundary condition ambiguity. |
| **Cobb-Douglas schedule** `f = √(a·r)·p_B` | Correct voice gating mechanism. Our modifications to liveness (adding credibility) flow through to the schedule naturally. |
| **Strawman detection** (liveness/grounding mismatch) | Still valid. Adding mechanical pathway diversity to grounding makes strawman detection even sharper — a pumped-liveness, zero-grounding claim is even more obviously a strawman when grounding requires mechanical diversity. |

### Architecture: Where the New Layer Sits

```
┌──────────────────────────────────────────────────────────────────┐
│                  EXISTING (Connor's Architecture)                  │
│                                                                    │
│  Points + Operators + LMSR Markets                                 │
│       │                                                            │
│       ▼                                                            │
│  Liveness (w) + Grounding (g) + Track Records (h)                 │
│       │                                                            │
│       ▼                                                            │
│  Doubt (adversarial trials, κ-detection, slash-to-B)              │
│       │                                                            │
│       ▼                                                            │
│  Resolution (reads signal, enacts decisions)                       │
└──────────────────────────────────────────────────────────────────┘

                    ↓ OUR ADDITION ↓

┌──────────────────────────────────────────────────────────────────┐
│              NEW — MECHANICAL DECOMPOSITION LAYER                  │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  Pre-NAND Decomposition                                     │  │
│  │  - Extract mechanical models from contradicting sources      │  │
│  │  - Identify boundary conditions                              │  │
│  │  - Route to: qualification (boundary found) / NAND (genuine) │  │
│  └─────────────────────────────────────────────────────────────┘  │
│       │                                                            │
│       ▼                                                            │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  Multi-Dimensional Credibility                               │  │
│  │  - Per (source, mechanical_domain) credibility vectors       │  │
│  │  - Modifies liveness: w = p_B · p_R · credibility(B, domain) │  │
│  └─────────────────────────────────────────────────────────────┘  │
│       │                                                            │
│       ▼                                                            │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  Mechanical Pathway Diversity                                │  │
│  │  - Groups resolution events by underlying mechanism          │  │
│  │  - Modifies grounding: pathway-weighted M matrix             │  │
│  └─────────────────────────────────────────────────────────────┘  │
│       │                                                            │
│       ▼                                                            │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  Extended Provenance                                         │  │
│  │  - Per-position: mechanical model + boundary conditions      │  │
│  │  - Modifies track records: mechanical survivorship tracking  │  │
│  └─────────────────────────────────────────────────────────────┘  │
│       │                                                            │
│       ▼                                                            │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  Pre-Adversarial Decomposition                               │  │
│  │  - Attempts mechanical resolution before adversarial trial   │  │
│  │  - Routes easy cases (boundary ambiguity) to qualification   │  │
│  │  - Routes hard cases (genuine contradiction) to Doubt layer  │  │
│  └─────────────────────────────────────────────────────────────┘  │
│       │                                                            │
│       ▼                                                            │
│  (flows into Connor's existing Doubt → Resolution pipeline)       │
└──────────────────────────────────────────────────────────────────┘
```

### Summary: What We're Actually Changing

| Layer | Connor's version | Our version | Why |
|-------|-----------------|-------------|-----|
| **Liveness** | `w = p_B · p_R` | `w = p_B · p_R · cred(B, domain)` | Don't treat all counterpoints as equally worth testing |
| **Operators** | One type: R(A,B) | Six types: R, S, C, Q, M, D | Relevance alone can't distinguish contradiction from qualification |
| **Contradiction** | NAND applied immediately | Pre-NAND decomposition first | Most disagreements are boundary condition ambiguities, not logical contradictions |
| **Grounding** | PageRank over relevance | PageRank over relevance × pathway diversity | Three tests of the same mechanism ≠ three independent validations |
| **Track records** | Survived adverse trial? | Survived AND mechanical model was sound? | Don't reward luck as much as mechanical correctness |
| **Doubt** | Purely adversarial | Collaborative pre-processing → adversarial for hard cases | System should try to understand before escalating to betting |
| **Signal/price** | Unchanged | Unchanged | Still correct |
| **Markets** | Unchanged | Unchanged | Still the right aggregation mechanism |
| **Position-ledger** | Unchanged | Unchanged (entries enriched) | Still correct design |

---

*Last updated: 2026-07-06*
