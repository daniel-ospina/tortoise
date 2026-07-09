# Zep

> AI agent memory platform — temporal knowledge graphs at enterprise scale. "The data-lake pattern, applied to agent context."

---

## 1. Overview

| Field | Value |
|---|---|
| Founded | 2023 |
| HQ | San Francisco, CA (2261 Market Street) |
| Funding raised | ~$500K Seed (Y Combinator W24, March 2024) |
| Team size | ~5 employees |
| Markets | Global (enterprise AI/ML teams) |
| Key milestone | YC W24; S&P Global Market Intelligence coverage April 2026; Graphiti crossed 20K GitHub stars |

*Last checked: 2026-07-06*

---

## 2. Product Type

**Zep Cloud** — Managed platform providing persistent agent memory via temporal context graphs. Ingests chat history, business data, and user interactions → constructs a temporal knowledge graph → serves token-efficient context at sub-200ms P95 latency.

**Graphiti** — Open-source temporal knowledge graph engine (Apache-2.0). Powers Zep's infrastructure. `pip install graphiti-core`. Supports Neo4j, FalkorDB, and AWS Neptune backends. Tracks bi-temporal validity — facts have `valid_from`/`valid_to`; queries can ask "what was true on [date]?"

**Relationship:** Graphiti = open-source engine (single-subject graphs, local dev). Zep Cloud = managed, governed, enterprise-hardened platform (multi-tenant, ABAC, retention, audit, compliance).

*Last checked: 2026-07-06*

---

## 3. Positioning & Messaging

**Tagline:** "Agent memory, at enterprise scale."

**Value proposition (their words):** "Memory of users, the business, and work done. Managed, governed, and served at scale."

**Brand voice:** Enterprise infrastructure. Heavy emphasis on governance, compliance, and scale. SOC 2, HIPAA, S&P coverage, and a live demo dashboard (not cartoon illustrations). Tone: "serious infrastructure for production AI teams."

**Alternative/comparison pages** (competitive posture):
- Zep vs Mem0 Alternative
- Zep vs Letta Alternative
- Zep vs AWS AgentCore Alternative
- Zep vs Vertex AI Memory Bank Alternative
- Zep vs Cognee Alternative
- Zep vs Supermemory Alternative

[Source](https://www.getzep.com/) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 4. Target Audience

**Primary:** Enterprise AI/ML teams building production agent systems.

| Segment | Details |
|---------|---------|
| Personas | AI platform engineers, MLOps, CTOs of AI-native startups, enterprise architects |
| Use cases | Customer support agents, personalized chatbots, multi-turn AI assistants, deal-flow analysis, portfolio review |
| Stack | Framework-agnostic — LangGraph, CrewAI, AutoGen, Microsoft Agent Framework, Google ADK, Pydantic AI, Mastra, Vercel AI SDK, LiveKit |
| Named customers | Torq, AlphaSignal, Flockx, Axtria |

**Not for:** Hobbyist chatbot builders or simple RAG. Credit-based pricing and enterprise compliance posture target teams shipping agents to production.

*Last checked: 2026-07-06*

---

## 5. Business Model & Pricing

**Revenue model:** Credit-based SaaS (ingestion metered by Episode size) + enterprise contracts. Open-source engine (Graphiti, Apache-2.0) as developer acquisition funnel.

### Pricing Tiers

| Tier | Price | Credits/mo | Key Limits | Key Features |
|------|-------|------------|------------|--------------|
| **Free** | $0 | 10,000 | 2 projects, 5 entity/edge types, variable rate limits, lower-priority processing | No rollover, no auto top-up |
| **Flex** | $104/mo (annual) / $125/mo (monthly) | 50,000 | 5 projects, 600 RPM, 10 entity/edge types | Auto top-up at 20% ($25/10K credits), 30-day rollover, 1-day API logs |
| **Flex Plus** | $312/mo (annual) / $375/mo (monthly) | 200,000 | 10 projects, 1,000 RPM, 20 entity/edge types | Auto top-up ($75/40K credits), 60-day rollover, 7-day API logs, Observations, Webhooks, Analytics, Custom extraction instructions |
| **Enterprise** | Custom | Custom with negotiated rates | Unlimited projects, guaranteed RPM/SLA | SOC 2 Type II, HIPAA BAA, 1-year audit logs, DPA (EU), Slack/Teams + dedicated AM, BYOC/BYOK deployment |

**Credit mechanics:** 1 credit per Episode up to 350 bytes. +1 credit per additional 350 bytes. ⅛ credit per webhook invocation. 0 credits for retrieval, storage, threads, users, graph storage.

**Deployment options:** Managed Cloud (Zep's infra, SOC 2 + HIPAA), BYOK (Zep's cloud + customer KMS keys), BYOC (Zep inside customer VPC).

**Open source:** Zep Community Edition discontinued (code in `legacy/`). Graphiti: Apache-2.0, `pip install graphiti-core`, 20K+ GitHub stars.

⚠️ pricing page returned 404 — data from homepage + docs + web_search. [Source](https://www.getzep.com/) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 6. Product & Features

### Zep Cloud — Core Capabilities

| Capability | Details |
|------------|---------|
| **Context Lake** | Millions of context graphs managed as one system — data-lake pattern for agent context |
| **Context Graph Engine** | Proprietary engine built on Graphiti; sub-200ms P95 retrieval at any scale (tested to 100M graphs: 168ms) |
| **Temporal memory** | Bi-temporal tracking — facts have `valid_from`/`valid_to`; "what was true on [date]?" queries |
| **Episodes** | Any data object (chat message, JSON, text) ingested into the graph |
| **Observations** | Analyzes graph structure to surface patterns, recurrences, co-occurrences |
| **Entity & edge types** | Custom ontology — define PERSON, TOPIC, PRODUCT, COMPANY and relationships |
| **Provenance** | Every fact traces back to source episode — audit any answer to origin |
| **Access control (ABAC)** | Principal → Resource → Action → Policy (Allow/Deny) |
| **Retention** | Policy-driven expiration + Legal Hold for compliance |
| **Audit** | Detailed logs of every request and policy decision |
| **Observability** | Built-in dashboard: latency, error rate, retrieval activity, ingest throughput per project |
| **MCP Server** | `zep-mcp-server` for AI agent tool integration |
| **Webhooks** | Available on Flex Plus |

### SDKs & Languages
- Python: `pip install zep-cloud`
- TypeScript: `npm install @getzep/zep-cloud`
- Go: `go get github.com/getzep/zep-go/v3`

### Framework Integrations
Google ADK, Microsoft Agent Framework, AutoGen/AG2, CrewAI, LangGraph, LiveKit, Pydantic AI, Mastra, Vercel AI SDK

### Graphiti (Open-Source Engine)
20,000+ GitHub stars, 35+ contributors (including AWS, Microsoft, FalkorDB, Neo4j), ~25,000 weekly PyPI downloads. Backends: Neo4j, FalkorDB, AWS Neptune.

### Tech Stack
Python (64.2%), Go (19.3%), TypeScript (13.6%). Graph databases: Neo4j, FalkorDB.

[Source](https://www.getzep.com/), [GitHub](https://github.com/getzep/graphiti) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 7. Go-to-Market & Acquisition

**Primary growth channels:**

| Channel | Activity |
|---------|----------|
| **Open-source funnel** | Graphiti (20K+ stars) and zep repo (4.7K stars) → Zep Cloud conversion |
| **Comparison/alternative pages** | ~9 SEO-optimized pages: "Zep vs Mem0," "Zep vs Letta," "Mem0 Alternative," etc. |
| **Content strategy** | Technical blog + benchmark results (LoCoMo 94.7%, LongMemEval 90.2%) + docs |
| **S&P coverage** | S&P Global Market Intelligence report (April 2026) — enterprise credibility |
| **YC network** | Y Combinator W24 batch — founder network, demo day exposure |

**Sales motion:** Self-serve (Free/Flex tiers) for developers → Enterprise sales for SOC 2/HIPAA/BYOC deals.

*Last checked: 2026-07-06*

---

## 8. Traction & Scale

| Signal | Value |
|---|---|
| GitHub stars (zep) | 4.7K |
| GitHub stars (graphiti) | 20,000+ |
| GitHub contributors (graphiti) | 35+ (including AWS, Microsoft, Neo4j, FalkorDB) |
| Graphiti PyPI downloads | ~25,000/week |
| Estimated ARR | ~$1M (2024, Tracxn estimate) |
| Named customers | Torq, AlphaSignal, Flockx, Axtria |
| Revenue signals | YC W24, $500K seed, S&P coverage April 2026 |
| Press / notable mentions | S&P Global Market Intelligence: "de facto partner in enterprise agent stack" (April 2026) |
| ⚠️ Public revenue | No public revenue data beyond estimates |

[Source](https://www.getzep.com/), [GitHub](https://github.com/getzep/graphiti), [Tracxn](https://tracxn.com) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 9. SEO & Organic Presence

| Metric | Value |
|---|---|
| ⚠️ Estimated monthly traffic | Not in public indices (niche enterprise tool) |
| ⚠️ Domain Authority | No public data |
| Top ranking keywords | "agent memory," "AI memory platform," "context graph," "temporal knowledge graph," "[competitor] alternative" |
| Content strategy | ~9 comparison landing pages + technical blog + docs portal + benchmark pages |

**Keyword overlaps with El Dato:** None — Zep operates in the AI agent infrastructure space, not local commerce or deals.

⚠️ Specific DA/DR and traffic figures require paid SimilarWeb/Ahrefs access. [Source](https://www.getzep.com/) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 10. Social & Community

| Channel | Followers | Engagement notes |
|---|---|---|
| LinkedIn | 3,812 | Company page, modest following |
| X/Twitter (@zep_ai) | 2,363 | 178 following; technical content |
| GitHub (graphiti) | 20,000+ stars | 35+ contributors — strong OSS community |
| GitHub (zep) | 4.7K stars | 636 forks, 21 contributors |
| ⚠️ Discord | No public community found | |
| ⚠️ Slack | No public community found | |

**Community mechanics:** Graphiti open-source community is the primary engagement channel — unusual contributor breadth (AWS, Microsoft, Neo4j, FalkorDB) for a 5-person startup. Community Edition discontinued; no replacement community forum.

[Source](https://linkedin.com/company/getzep), [X/Twitter](https://x.com/zep_ai), [GitHub](https://github.com/getzep) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 11. Customer Sentiment

**Sources checked:** getzep.com testimonials, GitHub issues, web search, Reddit (r/LLMDevs)

**What users/businesses praise:**
- Temporal knowledge graph approach — tracks when facts were true, not just what is true
- Sub-200ms P95 latency at any scale (benchmarked to 100M graphs)
- Enterprise governance: SOC 2, HIPAA, ABAC, audit logs — unusual for a startup
- Benchmark leadership: LoCoMo 94.7%, LongMemEval 90.2%
- Open-source Graphiti with AWS/Microsoft contributors = strong developer validation

**What users/businesses complain about:**
- Community Edition discontinued — some open-source users frustrated by cloud-only shift
- ⚠️ No public review sites (G2, Capterra, ProductHunt) — enterprise infra tools rarely get public reviews
- Small team (5 people) supporting managed cloud + 20K-star OSS project = potential scaling risk

**Overall sentiment:** Positive but thin. Strong enterprise signals (S&P coverage, named customers, compliance certs) and OSS traction. No significant negative sentiment surfaced. Too early/small for substantial review volume.

[Source](https://www.getzep.com/), [Reddit](https://reddit.com/r/LLMDevs) — retrieved 2026-07-06

*Last updated: 2026-07-06*

---

## Notes & Sources

- **Primary:** [getzep.com](https://www.getzep.com/) — homepage, fetched 2026-07-06
- **Pricing:** ⚠️ help.getzep.com/pricing returned 404. Data from homepage + docs + web_search. Verify against live pricing page when accessible.
- **Graphiti:** [github.com/getzep/graphiti](https://github.com/getzep/graphiti) — 20K+ stars, Apache-2.0
- **Tracxn:** Company profile — funding, team size, revenue estimates
- **LinkedIn:** [linkedin.com/company/getzep](https://linkedin.com/company/getzep) — 3,812 followers
- **X/Twitter:** [x.com/zep_ai](https://x.com/zep_ai) — 2,363 followers
- **⚠️ grafiti.ai does not resolve** — no acquisition. Graphiti is Zep's homegrown open-source engine, not an acquired product.
- **⚠️ S&P report** referenced on homepage but full report behind paywall. Snippet: "Zep tackles agent memory limitations through its temporal context graph... We can easily see Zep becoming a de facto partner in this layer of the enterprise agent stack." — Melissa Incera, April 2026.

*Last updated: 2026-07-06*
