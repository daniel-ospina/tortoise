# Emergence AI

> Enterprise "agentic infrastructure" lab — Orchestrator, Emergence Agents, Emergence Assistant, Semantic Intelligence, all delivered on the CRAFT platform. Memory is a retrieval layer over stored records ("Context Packs"), explicitly *not* a knowledge graph.

---

## 1. Overview

| Field | Value |
|---|---|
| Founded | 2023 (exited stealth June 2024). ⚠️ Tracxn and TheCompanyCheck list 2018 — likely the Merlyn Mind incorporation date, since Emergence began as "a division of the educational chatbot company Merlyn Mind Inc." |
| HQ | New York, NY. Additional offices: Irvine, Bengaluru, Madrid ([LinkedIn](https://www.linkedin.com/company/emergenceai)) |
| Funding raised | **$97.2M round led by Learn Capital** (June 2024), plus **> $100M in lines of credit** ([SiliconANGLE, 2024-06-24](https://siliconangle.com/2024/06/24/ai-startup-emergence-ai-raises-ton-cash-enhance-office-worker-productivity/)). No later round disclosed as of 2026-09-11. |
| Team size | 107 ([GetLatka](https://getlatka.com/companies/emergence.ai)) / 123 as of 2026-07-31 ([Tracxn](https://tracxn.com/d/companies/emergence-ai/__kDwW8PRyndeyEU7117oiy1c6iwmi1ECADhDa4m09MWI)) / "51–200" band (LinkedIn) |
| Leadership | Satya Nitta (co-founder; **Executive Chairman** on the About page — LinkedIn and Jan-2026 press still call him CEO), Ravi Kokku (co-founder, CTO), Sharad Sundararajan (co-founder, CPO), **Ian Eslick (CEO)** |
| Markets | Global enterprise: semiconductor manufacturing, data governance/analytics, regulated industries (banking, healthcare, aviation, government) in MENAT via e& enterprise |
| Key milestone | 2026 repositioning to "neuroformal AI for mission-critical environments"; Bengaluru research hub + 500-researcher hiring plan (Mar 2026); e& enterprise MENAT distribution deal (Jan 2026) |

⚠️ **Name-collision hazard:** "Emergent" (emergent.sh, the Indian vibe-coding startup that raised $230M from SoftBank/Khosla) is a **different company**. Perplexity conflates the two on generic funding queries — all funding figures above were verified against an article naming Emergence AI's leadership.

[Source](https://www.emergence.ai/about-us) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 2. Product Type

**CRAFT** — their platform brand. Self-described as "an enterprise intelligence platform" with three modules ([docs introduction](https://docs.emergence.ai/getting-started/introduction)):

| Module | Function | Status |
|---|---|---|
| **CRAFT Assess** | Evaluates data and surfaces what blocks "agent-readiness" | Live |
| **CRAFT Enrich** | Enriches metadata, generates data-quality rules, classifies assets | Live |
| **CRAFT Toolkit** | Verification certificates and auto-formalization tools | **Planned** |

**3-layer architecture** ([docs architecture](https://docs.emergence.ai/getting-started/architecture)):
1. **Solutions** — domain apps: Data Insights, Data Governance, Semiconductor Analytics
2. **Platform** — identity (Keycloak), authorization (OpenFGA ReBAC), secrets (Infisical / ESO), agent registry, schedules, LLM gateway (LiteLLM), **Memory Service** (inside `em-runtime-utils`)
3. **Infrastructure** — Kubernetes + Helm + ArgoCD GitOps, four environments (dev, staging, demo, prod)

**Front-end products** (site nav): Emergence Agents, Emergence Assistant, Semantic Intelligence, Emergence Platform. Historical products: **Agent-E** (web-control agent, May 2024, MIT, open-sourced) and the **Orchestrator** (flagship, December 2024).

**Memory Service** — a "Context Pack"-based memory store for multi-agent use. Notably, memory is **not a separate service**: "Memory is built into the **Utils service** (`em-runtime-utils`). No separate service is required" ([docs](https://docs.emergence.ai/platform/memory-service)).

**Deployment:** on-prem / air-gapped / own cloud, or via GCP, AWS, Azure marketplaces as private offers.

[Source](https://docs.emergence.ai/getting-started/introduction), [Source](https://www.emergence.ai/emergence-platform) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 3. Positioning & Messaging

**Tagline (page title):** "Neuroformal AI for Mission-Critical Environments"

**H1 (their words):** "Building verified autonomy for mission-critical enterprise systems"

**Sub-headline:** "The frontier AI lab turning cutting-edge agentic research into enterprise infrastructure"

**Secondary H1 (About page):** "Building the verified control layer enterprise infrastructure depends on"

**Mission statement (verbatim):** "Emergence was built on a single belief: the defining challenge of this moment in computing is not capability, it's control. Autonomous systems are becoming powerful enough to act at the speed of modern enterprise. But what's missing is the critical infrastructure to ensure they act within verified bounds. This is the problem Emergence exists to solve. Not autonomy for its own sake, but verified autonomy."

**Their memory claim (verbatim, homepage):** "AI is only as powerful as what it remembers" / "We are the leading experts in context management and long-term memory. Emergence just set the state-of-the-art standard for AI memory and context management (86% accuracy) on the LongMemEval benchmark."

**Brand voice:** Research-lab authority crossed with defense/industrial assurance language — "determinism", "governed everywhere", "verified", "formally verified", "safety built for all engineering environments". Three stated pillars: **Determinism** ("operate predictably and verifiably… especially where failure is unacceptable"), **Governed everywhere**, **Continual self-improvement** ("Persistent memory systems that retain context, validated decisions, and learned remediations – converting every execution into durable institutional advantage").

**Positioning trajectory:** 2024–25 messaging was agent-orchestration led ("play well with others", "agents that build other agents"); 2026 messaging is verification/control-layer led, with **semiconductors as the lead industry spotlight**. The memory strand survived the pivot but was demoted from headline to one of several proof points.

[Source](https://www.emergence.ai/) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 4. Target Audience

**Primary customers:** Large regulated enterprises deploying autonomous agents against mission-critical processes.

| Segment | Details |
|---|---|
| Industries | Semiconductors (design → ramp → production → sustaining yield), financial services, healthcare, aviation, government |
| Personas | Business leaders, knowledge workers, data teams, data-governance teams, platform engineers ([docs "Who is it for?"](https://docs.emergence.ai/getting-started/introduction)) |
| Named customers/partners | Samsung, Newline Interactive (2024, display products — [TechCrunch](https://techcrunch.com/2024/06/24/emergence-thinks-it-can-crack-the-ai-agent-code/)); **Andela** (Jun 2025 upskilling partnership); **e& enterprise** (Jan 2026 MENAT distribution); NI/Emerson ⚠️ single-source ([Growth Engineer](https://growthengineer.ai/startups/emergence)). Their semiconductor customers are described only as "leading semiconductor companies" — never named. |
| Integrations claimed | "Integrates with OptimalPlus GO and other yield platforms; Connects EDA outputs, test systems, MES, QMS, and PLM; Captures unstructured knowledge: FA reports, tickets, SOPs, tribal knowledge" ([semi-overview](https://www.emergence.ai/semi-overview)) |

**Not for:** individual developers, hobbyists, or self-serve experimentation. ⚠️ **No signup, trial, or get-started path exists** — `/signup`, `/trial`, `/get-started` all return 404 (checked 2026-09-11). Access is via "Partner with us" or a marketplace private offer.

[Source](https://www.emergence.ai/semi-overview), [Source](https://docs.emergence.ai/getting-started/introduction) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 5. Business Model & Pricing

**Revenue model:** Enterprise contracts, negotiated per customer. Deployment is BYOC/on-prem/air-gapped, so there is no metered usage tier visible publicly. Distribution is also partner-led (e& enterprise) and marketplace-mediated.

**Pricing tiers: NONE PUBLIC.** Probe results, 2026-09-11:

| URL | Result |
|---|---|
| `emergence.ai/pricing` | 404 |
| `emergence.ai/plans` | 404 |
| `emergence.ai/get-started` | 404 |
| `emergence.ai/signup` | 404 |
| `emergence.ai/trial` | 404 |
| `emergence.ai/contact` | 404 |

⚠️ **Pricing gap — explicitly documented:** no public price, no free tier, no usage-based credit system, no self-serve checkout. There is a `/cart` route on the site but no purchasable public SKUs.

**How they actually sell (verbatim from the GCP marketplace doc):** "Private offers allow the sales team to send a pre-configured pricing link directly to target customers. **No public storefront is required.**" Purchases can be drawn against existing GCP committed spend "with a 25% cap for channel deals". Equivalent docs exist for AWS Marketplace ("EDP committed spend drawdown") and Azure Marketplace ("MACC committed spend drawdown").

**Open-source surface:** Agent-E (MIT, 1,250★), Emergence-World (597★), `emergence_simple_fast` (13★). These are credibility/recruiting artifacts, not a product funnel — no OSS-to-cloud conversion path is published.

**Revenue estimate:** ~$11.8M est. ARR (2025) — ⚠️ single-source ([GetLatka](https://getlatka.com/companies/emergence.ai)) — verify against live site when accessible.

[Source](https://docs.emergence.ai/guides/marketplace/gcp) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 6. Product & Features

### 6.1 Memory Service / Context Packs (the competitive overlap)

**Context Pack = "the fundamental storage unit for agent memories"** — a named, typed container of memory records.

**Three-layer pack structure** ([docs](https://docs.emergence.ai/platform/memory-context-packs)):

| Layer | Contents | When loaded |
|---|---|---|
| **Summary** | High-level description, topic tags, last-updated timestamp | Always (fast discovery) |
| **Metadata** | Memory count, type distribution, confidence scores, access patterns | On pack selection |
| **Content** | Full memory records with embeddings, provenance, relationships | On demand |

**Two-phase query model:** (1) *Discover* — query pack summaries for relevant packs; (2) *Retrieve* — semantic search within the matched pack's content layer. Plus direct retrieval by `name` within a pack.

**Pack types and decay rates:** `session` (aggressive, hours–days), `user`, `project`, `team`, `data_source` (conservative, months), `domain` (very conservative, years).

**Memory record schema (verbatim from docs):**

```json
{
  "id": "uuid",
  "content": "The main memory text (self-contained, context-independent)",
  "memory_type": "fact | experience | observation | instruction | preference | summary | glossary | ontology | textual_pattern | kpi | exemplar | join | numeric_pattern | policy",
  "name": "optional_label_for_direct_retrieval",
  "metadata": {
    "source": "conversation | document | agent_message",
    "confidence": 0.92,
    "created_at": "2026-04-07T10:30:00Z",
    "last_accessed": "2026-04-07T14:22:00Z",
    "access_count": 7,
    "superseded_by": null,
    "provenance": "Session #142, turn 3"
  },
  "embedding": [0.123, -0.456, ...],
  "relationships": [
    {"type": "supersedes", "target_id": "uuid-of-old-memory"},
    {"type": "related_to", "target_id": "uuid-of-related-memory"}
  ],
  "degradation_tier": "full | summary | key_facts | existence_marker | archived"
}
```

**Relationship vocabulary: exactly two types** — `supersedes` and `related_to`, plus a `superseded_by` back-pointer in metadata and API endpoints `/memories/{old}/supersede/{new}`, `/memories/{id}/pin`, `/memories/{id}/archive`. **There is no contradiction / negation / defeats / refutes edge type, and no counter-argument construct.**

**Degradation tiers (memory lifecycle):** `full` → `summary` (~30% storage) → `key_facts` (~10%) → `existence_marker` (minimal) → `archived` (off-heap, not retrieved). "Any retrieval access re-promotes a memory to the **full** tier." Creation is gated by a **worthiness score**.

**Confidence:** a stored per-record float (`confidence: 0.92` in the schema). It is a field written at extraction time, **not a computed or propagated quantity** — no inference network, no belief updates, no cross-record propagation is documented anywhere in the Memory Service docs.

**Provenance:** a free-text `metadata.provenance` string (e.g. `"Session #142, turn 3"`). Real and useful, but descriptive rather than a first-class traversable edge.

### 6.2 Other capabilities

| Area | Capabilities |
|---|---|
| **Agents** | A2A protocol agent registry (agents + MCP servers + agentskills.io skills), multi-agent patterns (delegation, supervision, parallel fan-out), pipeline framework with cooperative cancellation, agent eval harness (Langfuse) |
| **Data Insights** | Verified Talk-to-Data, schema-aware NL→SQL with `sqlglot` validation, Analysis Agent (LLM-generated Python reasoning loops), Plotly visualizations |
| **Data Governance** | Column/table profiling, LLM metadata enrichment, DQ rule generation, scorecards, Prefect orchestration |
| **Semiconductors** | Spec-to-RTL generation, verification speedup, timing-closure debug, test-plan generation, ATE program validation, excursion detection & triage, defect pareto, yield improvement |
| **Security/compliance** | Keycloak OIDC/SSO, OpenFGA ReBAC, secrets management (ESO + GCP SM, or Infisical), multi-tenant isolation, audit log with retention settings, SOC 2 / HIPAA / GDPR mapping docs |
| **Observability** | OpenTelemetry (OTLP), Langfuse, Grafana LGTM, LiteLLM gateway with project-scoped cost attribution |

### 6.3 Notable gaps

- **No knowledge graph in production for memory.** Verbatim from their own memory research post: after describing sentence decomposition into subject/relation/object/complement/adjunct, "This breakdown of sentences allows us to build a semantic graph with continuous representations of each of its nodes and links, but as you will see **we didn't need to go that far to establish a new state of the art on LongMemEval**…" ([blog, Jun 19 2025](https://www.emergence.ai/blog/og2q4h3p2zcmkxjdvj099ogqm21a4a))
- **No contradiction handling.** Two edge types only; no NAND-style defeat, no conflict surfacing.
- **No belief propagation.** `confidence` is static per record.
- **No temporal validity interval on facts.** Only `created_at` / `last_accessed` timestamps and free-text provenance — no `valid_from` / `valid_to`; supersession is a link, not a validity window.
- **"Open-source components" is ambiguous marketing.** Docs say "The core platform components are open-source and self-hostable" but the surrounding list is third-party dependencies (Keycloak, OpenFGA, LiteLLM, Prefect, Redis Streams, `obstore`). **CRAFT itself has no public repository** — GitHub org `EmergenceAI` contains no CRAFT repo (verified 2026-09-11).
- **CRAFT Toolkit is still "Planned"** — the verification-certificate and auto-formalization story ("mathematical proof is embedded into the architecture") is partially roadmap.
- **Webhooks are "Planned."**

[Source](https://docs.emergence.ai/platform/memory-context-packs), [Source](https://docs.emergence.ai/platform/memory-service), [Source](https://github.com/EmergenceAI) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 7. Go-to-Market & Acquisition

**Primary growth channels:**

| Channel | Activity |
|---|---|
| **Enterprise direct sales** | Private offers via GCP/AWS/Azure marketplaces; no public storefront. "No public storefront is required." |
| **Regional distribution partners** | **e& enterprise** (Jan 2026) — distribution + implementation + managed services for MENAT, targeting banking, healthcare, aviation, government; on-prem and **air-gapped** deployments |
| **Research-led brand** | Blog (~40 posts), Papers page (~15 papers), **Emergence World** benchmark/lab (`world.emergence.ai`, repo 597★), LongMemEval SOTA claim |
| **Open source as credibility** | Agent-E (MIT, 1,250★) and `emergence_simple_fast` — no published OSS→product funnel |
| **Industry spotlight pages** | Semiconductors as the flagship vertical landing experience (`/semi-overview`) |
| **Talent/scale play** | Bengaluru research hub (Mar 2026) + announced plan to hire **500 researchers** ([Times of India, Mar 2026](https://timesofindia.indiatimes.com/technology/tech-news/emergence-ai-to-hire-500-researchers-at-new-india-ai-lab/articleshow/129655313.cms)) |
| **Upskilling partnerships** | Andela (Jun 2025) — partner engineers trained on their stack |

**Sales motion:** Sales-led / partner-led, enterprise-only. There is no product-led or self-serve motion — every self-serve URL is 404.

[Source](https://www.emergence.ai/latest-news), [Source](https://docs.emergence.ai/guides/marketplace/gcp) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 8. Traction & Scale

| Signal | Value |
|---|---|
| Funding | $97.2M led by Learn Capital + >$100M credit lines (Jun 2024) |
| Est. ARR | ~$11.8M (2025) — ⚠️ single-source (GetLatka) |
| Employees | 107 (GetLatka) / 123 (Tracxn, 2026-07-31) / 51–200 band (LinkedIn) |
| LinkedIn followers | 12,609 |
| X/Twitter followers | 463 |
| GitHub org followers | 198 |
| Discord members | 689 (59 online) — checked 2026-09-11 |
| GitHub (Agent-E) | 1,250★, 191 forks, MIT |
| GitHub (Emergence-World) | 597★, 74 forks, updated 2026-09-10 |
| GitHub (`emergence_simple_fast`) | **13★, 5 forks, no license file** — the repo for their published SOTA method |
| Benchmark | LongMemEval 86% (EmergenceMem Internal, Jun 2025) — **superseded**: Zep reports 90.2% |
| Named partners | Samsung, Newline Interactive, Andela, e& enterprise, NI/Emerson ⚠️ |
| Press | TechCrunch (Jun 2024 exit from stealth); SiliconANGLE (Jun 2024 funding); VentureBeat (Dec 2024 Orchestrator, Jun 2025 CRAFT); Business Insider "51 most disruptive startups of 2024"; EE Times (Bengaluru hub); Times of India (500 researchers); Gulf News (e& deal); Bloomberg mention (Sep 2025) |

⚠️ **No public revenue, customer count, ARR, or retention data.** No named semiconductor customers despite the vertical being the lead spotlight. The Mercedes item on their news page (Bloomberg, 2025-09-24, "Mercedes Replaces Technology Chief, Promotes CEO Ally") is a personnel story and **cannot be attributed as a customer relationship** — left unclaimed.

[Source](https://siliconangle.com/2024/06/24/ai-startup-emergence-ai-raises-ton-cash-enhance-office-worker-productivity/), [Source](https://www.emergence.ai/latest-news), [GitHub](https://github.com/EmergenceAI) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 9. SEO & Organic Presence

| Metric | Value |
|---|---|
| ⚠️ Estimated monthly traffic | Not in public indices — no paid SimilarWeb/Ahrefs access |
| ⚠️ Domain Authority | No public data |
| Content surfaces | `/blog` (~40 posts, categories Engineering / Insights / Product), `/papers` (~15 papers), `world.emergence.ai`, `docs.emergence.ai`, `/latest-news` (17 items) |
| Docs quality | **High.** Full REST API reference, architecture diagrams (Mermaid), compliance pages (SOC 2, HIPAA, GDPR), deployment/Helm values reference, solution-developer and agent-author guides, release notes |
| LLM-crawler friendliness | **`docs.emergence.ai/llms.txt` is published** and comprehensive (~80+ doc pages enumerated, each fetchable as `.md`) |
| Keyword territories | "agentic AI enterprise", "verified autonomy", "AI memory" / "LongMemEval", "context packs", "text-to-SQL enterprise", "data governance agents", "semiconductor AI agents", "yield excursion detection" |

**Observation:** their docs are an unusually strong organic asset for an enterprise vendor — they publish the *internal data model* (including the full memory record schema, degradation tiers, and confidence field) into the open web in machine-readable form. This is simultaneously their best SEO/LLM-visibility play and the reason a competitor can audit their epistemic model exactly rather than inferring it.

⚠️ Specific DA/DR and traffic figures require paid tools. No paid-traffic data available.

[Source](https://docs.emergence.ai/llms.txt), [Source](https://www.emergence.ai/blog) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 10. Social & Community

| Channel | Followers / Members | Engagement notes |
|---|---|---|
| LinkedIn | 12,609 | Primary audience; the company's dominant channel — consistent with enterprise sales-led motion |
| X/Twitter (@emergence_ai) | 463 | Very small relative to LinkedIn (≈3.7% of LinkedIn); product-announcement oriented |
| GitHub (org) | 198 org followers; Agent-E 1,250★ | Best-performing OSS artifact is Agent-E (web agent), not the memory work |
| Discord | 689 members / 59 online | Exists, modest, low concurrency |
| ⚠️ G2 / Capterra / ProductHunt | No public review presence found | |

**Community mechanics:** There is no community-led motion. OSS releases are research artifacts (Agent-E, `emergence_simple_fast`, Emergence-World) rather than platforms with contributor programs. The headline benchmark method — `emergence_simple_fast` — sits at **13 stars**, i.e. essentially no community reproduction or extension. Contrast with Zep's Graphiti (crossed 20K★, now ~31K★; ~62 contributors incl. AWS/Microsoft/Neo4j).

[Source](https://github.com/EmergenceAI), [Source](https://x.com/emergence_ai), [Source](https://www.linkedin.com/company/emergenceai) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 11. Customer Sentiment

**Sources checked:** their own blog/benchmark disclosures, an independent code-audit write-up, press coverage (TechCrunch, VentureBeat, Gulf News, EE Times), GitHub repos, Discord metadata, LinkedIn/X follower counts. ⚠️ **No G2, Capterra, TrustRadius, or ProductHunt reviews found** — no enterprise-customer sentiment dataset exists publicly. **No customer case studies, logos, or quotes are published on their own site.**

**What is praised:**
- Research credibility: LongMemEval SOTA at time of publication (86%), plus Agent-E's reported 61.1% independently-evaluated H Company task completion and 73.2% on WebVoyager ([their Orchestrator post](https://www.emergence.ai/blog/q7yz1gypvzqcdalwwh2mxocdai8eth))
- Technical documentation quality — deep, structured, machine-readable
- Enterprise deployment posture: on-prem/air-gapped, SOC 2/HIPAA/GDPR mappings, OpenFGA ReBAC, audit logs
- Open-source releases with real utility (Agent-E, MIT)

**What is criticized / where it is weak:**
- **Headline memory number is superseded and stale.** Their homepage as of 2026-09-11 still says they "just set the state-of-the-art standard … (86% accuracy)". Zep's current published figure is **90.2%** ([getzep.com/research](https://www.getzep.com/research/)), and Zep's 71.2% comparison point that Emergence used was already outdated when they published. Multiple benchmark listings still show the older 71.2% figure, i.e. the benchmark is contested on version/methodology.
- **The 86% is not reproducible: the model that achieved it is not published.** Only `EmergenceMem Simple Fast` (79%) was open-sourced — and that repo has 13 stars and no license.
- **Independent code audit found crude retrieval defaults.** ⚠️ *single-source — page is Cloudflare-blocked (403 via both `web_fetch` and `curl`); content available only via search snippet.* "[The] open-source code uses simple retrieval choices, including **no explicit chunking** and a **hardcoded k=42** retrieval limit, which the author argues may explain weaker performance on LoCoMo." ([Medium — "Emergence AI Broke the Agent Memory Benchmark. I Tried to Break Their Code"](https://medium.com/asymptotic-spaghetti-integration/emergence-ai-broke-the-agent-memory-benchmark-i-tried-to-break-their-code-23b9751ded97))
- **They concede their own method's limits, and argue the benchmark is the problem.** Verbatim: preference questions score lowest (≈70%); "We can do better, of course. There are questions we miss because the words in the question are not all that close (semantically) to the turn needed for accuracy. This is where having a continuous/latent knowledge graph helps quite a bit. But comparing our retrieval results with the 'oracle' information… we find that straightforward techniques missed only a few pertinent turns… **So, we might get 5 or so more questions correct, but what's the point?**" And: "**advanced memory architecture appears to be overkill for LongMemEval**. Thus, we are working towards a more enterprise-oriented benchmark."
- **Extraction loses temporal grounding.** Their own disclosure: facts extracted from conversations cannot resolve relative temporal references ("this Friday"), so they deliberately left them unresolved.
- **Product surface is opaque and gated.** No public pricing, no signup, no trial, no customer logos, no case studies — a buyer cannot self-assess without a sales conversation.
- ⚠️ Ambiguity in public comms: About page lists **Ian Eslick as CEO** while a Jan-2026 Gulf News article quotes **Satya Nitta as "Chief Executive"** — an unresolved leadership-transition signal for outsiders.

**Overall sentiment:** **Neutral-to-unknown, research-positive.** The company is highly credible as a research lab and has a strong enterprise posture, but there is essentially no public customer voice, no reproducible artifact behind the headline memory claim, and the headline claim has been surpassed. Memory is no longer the center of their positioning — in 2026 the lead vertical is semiconductors and the lead theme is verification.

[Source](https://www.emergence.ai/blog/og2q4h3p2zcmkxjdvj099ogqm21a4a), [Source](https://www.emergence.ai/blog/sota-on-longmemeval-with-rag), [Source](https://www.getzep.com/research/) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## Notes & Sources

**Primary pages fetched (2026-09-11):**
- [emergence.ai](https://www.emergence.ai/) — homepage; tagline "Neuroformal AI for Mission-Critical Environments"; the verbatim 86%/LongMemEval claim
- [emergence.ai/about-us](https://www.emergence.ai/about-us) — leadership, mission, 2023–2025 product timeline
- [emergence.ai/latest-news](https://www.emergence.ai/latest-news) — 17 press items
- [emergence.ai/semi-overview](https://www.emergence.ai/semi-overview) — semiconductor positioning
- [emergence.ai/emergence-agents](https://www.emergence.ai/emergence-agents), [/emergence-assistant](https://www.emergence.ai/emergence-assistant), [/emergence-semantic-intelligence](https://www.emergence.ai/emergence-semantic-intelligence), [/emergence-platform](https://www.emergence.ai/emergence-platform)
- [emergence.ai/blog](https://www.emergence.ai/blog) — ~40 posts

**Documentation (CRAFT / Memory Service):**
- [docs.emergence.ai/llms.txt](https://docs.emergence.ai/llms.txt) — full doc index (~80+ pages), each also available as `.md`
- [platform/memory-context-packs](https://docs.emergence.ai/platform/memory-context-packs) — Context Pack three-layer architecture, memory record schema, degradation tiers
- [platform/memory-service](https://docs.emergence.ai/platform/memory-service) — lifecycle, memory types, supersede/pin/archive endpoints
- [guides/memory-integration](https://docs.emergence.ai/guides/memory-integration) — integration pattern, best practices
- [getting-started/introduction](https://docs.emergence.ai/getting-started/introduction) — CRAFT modules, "open-source components" claim, personas
- [getting-started/architecture](https://docs.emergence.ai/getting-started/architecture) — 3-layer architecture, open-source component table
- [guides/marketplace/gcp](https://docs.emergence.ai/guides/marketplace/gcp) — "No public storefront is required", committed-spend drawdown

**Benchmark posts:**
- [SOTA on LongMemEval with RAG](https://www.emergence.ai/blog/sota-on-longmemeval-with-rag) — Jun 18 2025; 86% / 82.4% / 79%; turn-match → session-retrieve → NDCG → gpt-4o-2024-08-06; Zep 71.2%; Oracle GPT-4o 82.4%
- [State of the Art Results in Agentic Memory](https://www.emergence.ai/blog/og2q4h3p2zcmkxjdvj099ogqm21a4a) — Jun 19 2025; "we didn't need to go that far"; "advanced memory architecture appears to be overkill for LongMemEval"; 686,404 facts / 65,886 episodes / 19,829 conversations
- [Closing personalization performance gaps with memory](https://www.emergence.ai/blog/03pzpaqkiv4p8niyisbyyhsxtritfx) — Jul 17 2025; preference questions ~70%; user-profile memory experiment
- [getzep.com/research](https://www.getzep.com/research/) — Zep's current LongMemEval 90.2% (supersedes Emergence's 86%)

**Company/funding:**
- [SiliconANGLE 2024-06-24](https://siliconangle.com/2024/06/24/ai-startup-emergence-ai-raises-ton-cash-enhance-office-worker-productivity/) — $97.2M led by Learn Capital, >$100M credit lines, Merlyn Mind division, Samsung/Newline partnerships
- [TechCrunch 2024-06-24](https://techcrunch.com/2024/06/24/emergence-thinks-it-can-crack-the-ai-agent-code/) — stealth exit, $97.2M
- [Tracxn profile](https://tracxn.com/d/companies/emergence-ai/__kDwW8PRyndeyEU7117oiy1c6iwmi1ECADhDa4m09MWI) — 123 employees (2026-07-31), "founded 2018" ⚠️ conflicts with stealth-exit timeline
- [GetLatka](https://getlatka.com/companies/emergence.ai) — 107 employees, $11.8M est. ARR ⚠️ estimate only
- [LinkedIn](https://www.linkedin.com/company/emergenceai) — 12,609 followers, 51–200 employees, NY/Irvine/Bengaluru/Madrid
- [Gulf News 2026-01-23](https://gulfnews.com/business/uae-telco-major-e-partners-with-us-firm-emergence-for-new-ai-deal-1.500418104) — e& enterprise MENAT distribution, on-prem/air-gapped
- [Times of India](https://timesofindia.indiatimes.com/technology/tech-news/emergence-ai-to-hire-500-researchers-at-new-india-ai-lab/articleshow/129655313.cms) / [EE Times](https://www.eetimes.com/u-s-startup-emergence-ai-opens-research-hub-in-bengaluru/) — Bengaluru hub, 500 researchers

**GitHub (org: EmergenceAI, checked 2026-09-11):** Agent-E 1,250★/191 forks/MIT; Emergence-World 597★/74 forks; emergence_simple_fast 13★/5 forks/**no license**; MathViz-E 36★; kotlin_speech_features 29★; embodied-drone-agents 26★; emergence-benchmarks 3★/AGPL-3.0. **No CRAFT repository exists.**

**Gaps documented (do not fill by inference):**
1. ⚠️ **No public pricing anywhere** — `/pricing`, `/plans`, `/get-started`, `/signup`, `/trial`, `/contact` all 404 on 2026-09-11. Pricing is private-offer only.
2. ⚠️ **The independent Medium code-audit is Cloudflare-blocked** (403 to both `web_fetch` and browser-UA `curl`). Its findings are recorded from the search snippet only and flagged single-source; it should be re-verified from a mirror.
3. ⚠️ **No G2/Capterra/review-site sentiment** exists. No customer logos or case studies on their site.
4. ⚠️ **No public SemEval-style reproduction of the 86%** — the winning model (`EmergenceMem Internal`) is unpublished; only the 79% variant was released, to little community attention (13★).
5. ⚠️ **Workforce figures conflict** (107 / 123 / 51–200 band) — date-and-source dependent.
6. ⚠️ **Founded-date conflict** (2023 stealth exit vs 2018 Tracxn/TheCompanyCheck incorporation).
7. ⚠️ **"Open-source" is unverified in the strong sense** — docs claim "core platform components are open-source and self-hostable", but no CRAFT source is published; the surrounding text enumerates third-party OSS dependencies. Read as "built on OSS, portable", not "source-available product."
8. ⚠️ **NI/Emerson** as a customer is single-source (Growth Engineer, a third-party profile site).
9. ⚠️ Mercedes/Bloomberg item on their news page is a personnel story — **not** verifiable as a customer relationship; not claimed.

*Last updated: 2026-09-11*
