---
title: "Competitor: Mem0"
type: competitive-research
domain: product
status: complete
summary: "Mem0 — drop-in memory infrastructure for AI agents. Retrieval index over LLM-extracted facts; ADD-only + co-occurrence graph + Dream consolidation. No typed relations, no belief propagation."
created: 2026-07-09
updated: 2026-09-11
---

# Mem0

> Drop-in memory infrastructure for AI agents and apps — an LLM-at-write extraction pipeline plus multi-signal retrieval, with a schema-free co-occurrence entity graph layered on top. 65K GitHub stars, $24M raised, YC-backed.

---

## 1. Overview

| Field | Value |
|---|---|
| Founded | 2023 |
| HQ | San Francisco, CA |
| Funding raised | **$24M across Seed + Series A.** Seed led by Kindred Ventures; Series A led by Basis Set Ventures, with Peak XV Partners, GitHub Fund, and Y Combinator. Angels: Scott Belsky, Dharmesh Shah, Olivier Pomel (Datadog), Paul Copplestone (Supabase), James Hawkins (PostHog), Thomas Dohmke (ex-GitHub), Lukas Biewald (W&B). |
| Team size | ⚠️ Conflicting: YC lists **10 employees**; Inc42 lists **21**. Company careers page shows ~3 open roles (Engineering + Research), all San Francisco. |
| Founders | Taranjeet Singh (CEO) and Deshraj Yadav (CTO). Singh scaled an early GPT app store to 1M+ users and built EvalAI; Yadav created EvalAI at Georgia Tech and worked on Tesla's AI platform (Autopilot/FSD). |
| Markets | Global — AI infrastructure / agent memory. Verticals marketed: Customer Support, Healthcare, Education, Sales & CRM, E-Commerce. |
| YC batch | Listed as active YC company (San Francisco) |

[Source](https://www.ycombinator.com/companies/mem0), [Source](https://mem0.ai/series-a), [Source](https://mem0.ai/about-us), [Source](https://inc42.com/company/mem0/people/) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 2. Product Type

**AI agent memory infrastructure** — a managed memory layer + open-source SDK. Three surfaces:

| Surface | What it is | Install |
|---|---|---|
| **Mem0 Library (OSS)** | Self-hosted Python/TS library. Apache-2.0 | `pip install mem0ai` / `npm install mem0ai` |
| **Self-Hosted Server** | Docker Compose stack (API + Qdrant), auth on by default, admin wizard, API keys | `cd server && make bootstrap` |
| **Mem0 Platform (Cloud)** | Managed, zero-ops production service — proprietary optimizations not in the OSS SDK | app.mem0.ai |

Plus adjacent surfaces: **CLI** (`mem0-cli` / `@mem0/cli`), **MCP integration**, **OpenMemory**, **Gateway**, and **agent plugins/skills** (Claude Code, Codex, Cursor, Windsurf, OpenCode, Kimi).

**Category framing (their words):** "Drop-in memory infrastructure for AI agents and apps. Context that persists. Built for production."

**Key distinction vs. Zep/Graphiti:** Mem0 stores *extracted natural-language facts* in a vector store and ranks them. Graphiti (Zep) stores *typed, time-bounded edges* in a graph. Mem0's graph is a co-occurrence index over entities, not a knowledge model.

🔄 **Draft correction:** The old draft's "vector store + optional graph (Pro tier)" is outdated — the external graph-store integration (Neo4j/Memgraph/Kuzu/Apache AGE/Neptune, `enable_graph` flag) has been **replaced** by native, built-in Graph Memory that is "always on." See §6.

[Source](https://github.com/mem0ai/mem0), [Source](https://docs.mem0.ai/open-source/features/graph-memory) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 3. Positioning & Messaging

**Tagline:** "Drop-in memory infrastructure for AI agents and apps. Context that persists. Built for production." (homepage, exact)

**Page title / search positioning:** "Mem0 - AI Memory Layer for your Agents & Apps | Persistent Context"

**Value proposition (their words):**

> "Mem0 gives agents persistent memory without pipeline changes. Less redundant context, lower token costs, measurably faster responses."

> "Just like every application needs a database, every agentic application needs memory. Tomorrow's users will expect software to understand them, learn from past interactions, and evolve with their needs." — Taranjeet Singh, CEO

> "We're building the default memory layer for AI agents - making LLM memory accessible and reliable for every developer." — About page mission quote

**Three stated strategy principles** (from the $24M announcement, verbatim): **Make It Work** ("every agentic application needs memory"), **Make It Neutral** ("One memory layer that works across every model, every framework, every platform" — explicitly positioned against model labs locking memory to a provider), **Make It Portable** ("Just as contacts became portable across devices and services, memory will too").

**Brand voice:** Developer-first infrastructure + enterprise-grade claims held simultaneously. Evidence-forward ("Built for <developers> who want proof, not promises"), benchmark-heavy, and increasingly enterprise/compliance-flavored (SOC 2, HIPAA, BYOK, "Your data stays yours"). Contrast with Zep: Mem0 leads with *ease and token efficiency*, Zep leads with *temporal correctness and governance*.

**Recent messaging shift (2026):** From "personalization memory" → "token-efficient memory algorithm" + accuracy head-to-heads on LoCoMo/LongMemEval/BEAM, plus a new "Dream" consolidation narrative ("Mem0 now has a way to keep memory accurate as it grows").

[Source](https://mem0.ai/) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 4. Target Audience

**Primary:** Developers and AI platform teams building production agents — self-serve first, enterprise second.

| Segment | Details |
|---|---|
| Personas | AI/agent developers, AI platform engineers, startups shipping agents, enterprise AI teams (healthcare, support, CRM, e-commerce) |
| Use cases | Personalized assistants, customer-support bots, healthcare patient assistants, sales/CRM assistants, voice agents (LiveKit/Pipecat/ElevenLabs), coding agents (Claude Code, Cursor via plugins) |
| Stack | Framework-agnostic — LangChain, LangGraph, LlamaIndex, CrewAI, AutoGen/AG2, Agno, Camel AI, OpenAI Agents SDK, Google ADK, Mastra, Vercel AI SDK, Strands Agents, ChatDev, Dify, Flowise, n8n, Zapier, AWS Bedrock |
| Enterprise segment | "Fortune 500 companies" (claim, unnamed); named case study: **Trend Micro** (AWS Bedrock + Neptune company-memory chatbot) |

**Explicitly not for:** teams needing typed knowledge representation, time-bounded fact validity, or reasoning over contradictions — Mem0's own benchmarks show those are its weakest areas (§6).

**Notable:** "Sign up as an agent" — an AI agent can mint a Mem0 API key in <5 seconds with no email/dashboard/OTP (`mem0 init --agent`). This is a deliberate agent-economy acquisition bet, not just a human self-serve funnel.

[Source](https://mem0.ai/), [Source](https://docs.mem0.ai/integrations), [Source](https://aws.amazon.com/blogs/machine-learning/company-wise-memory-in-amazon-bedrock-with-amazon-neptune-and-mem0/) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 5. Business Model & Pricing

**Revenue model:** Open-core. Free Apache-2.0 library + Docker self-host (adoption funnel) → usage-metered managed Platform (Hobby → Starter → Pro) → Enterprise contracts (on-prem, SSO, SLA, audit logs). Plus a separate usage-based option "for teams whose traffic doesn't map cleanly to a fixed tier."

### Pricing Tiers (live pricing page)

| Tier | Price | Add requests/mo | Retrieval requests/mo | Projects | Key features |
|---|---|---|---|---|---|
| **Hobby** | **$0** | 10,000 | 1,000 | 1 | Unlimited end users, community support |
| **Starter** | **$19/month** | 50,000 | 5,000 | 1 | Unlimited end users, community support |
| **Pro** (Popular) | **$249/month** | 500,000 | 50,000 | Unlimited | Multiproject, private Slack support, advanced analytics, graph memory (entity linking), Dream (memory consolidation) |
| **Enterprise** | Custom | Unlimited | Unlimited | Unlimited | SLA, on-prem deployment, audit logs, custom integrations & SSO |

**Billing unit:** *requests*, not memories. Free tier = 10,000 **add requests** + 1,000 **retrieval requests** per month.

### ⚠️ Documented internal inconsistency — "graph memory" gating

Mem0's own pages **disagree** about whether Graph Memory is Pro-gated:

- **Pricing page** comparison table shows `Graph memory` and `Dream` rows as **Pro / Enterprise only** (dashes under Hobby/Starter).
- **Docs (Graph Memory)** states: *"Graph memory: entity extraction, linking, and the retrieval boost — **All plans**, automatic"* and *"Graph view: interactive visualization in the dashboard — **Pro and Enterprise**."*
- **Docs (Dream)** states: Supersede and Merge are **"Always on, all plans"**; only **Synthesis is Pro+**.

**Best reading:** the underlying entity-linking/retrieval-boost and Supersede/Merge run on all plans; the **dashboard Graph view**, **Dream Synthesis**, and **Dream dashboard** are the actual Pro gates. Third-party pages (Atlan) flatly report "graph memory is locked to the Pro tier," which conflicts with the docs. ⚠️ **This ambiguity is itself a competitive weakness** — it is cited as the top community complaint (see §11).

**Compliance:** SOC 2 **Type I** (not Type II), HIPAA Ready, GDPR Ready. Deployment: Kubernetes, private cloud, or air-gapped ("Same API everywhere"). BYOK.

🔄 **Draft correction:** Draft said "Free tier: 1000 memories. Pro: usage-based." **Both wrong.** Free is 10,000 add + 1,000 retrieval *requests* (not 1000 memories); the ladder is $0 → $19 → $249 → Enterprise, with usage-based as a separate talk-to-sales path, not the Pro tier.

[Source](https://mem0.ai/pricing) — retrieved 2026-09-11; [Source](https://docs.mem0.ai/platform/features/dream) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 6. Product & Features

### Architecture — the 2026 rewrite (this invalidates most pre-2026 descriptions)

In **April 2026** Mem0 replaced its algorithm. The old pipeline was extract → **compare/UPDATE/DELETE** (the "2-call" loop). It is now:

**Extraction (write path) — six stages:**
1. **Store New Memories** — conversation enters async, after the agent responds
2. **Context Lookup** — find related existing memories (dedup context)
3. **Distill Memories** — **single-pass ADD-only LLM extraction** (one call; no UPDATE, no DELETE)
4. **Deduplicate + Embed** — hash-based dedup, then vectorize
5. **Graph Memory (Entity Linking)** — extract entities, embed, link across memories
6. **Temporal Reasoning** — separate pass reads each memory + source conversation date → stores temporal metadata: when the event occurred, ongoing/completed, timing precision, and memory type (event, state, plan, preference, relationship, absence)

**Retrieval (read path) — four signals fused by rank scoring:**
- **Semantic** (vector similarity)
- **BM25 keyword** (verb-form lemmatization)
- **Entity search** (graph co-occurrence boost)
- **Temporal reasoning** (query temporal intent classified with **no extra LLM call**; ranks the correct dated instance)

**Storage layers:** Vector DB (memory text, embeddings, metadata: timestamps, hash, categories, `attributed_to`) · Graph/Entity store (entities + embeddings + linked memory IDs) · SQL (ADD-event history log + rolling message window, for audit + extraction dedup context).

### Benchmark scores (self-reported, managed Platform, top_200 retrieval budget)

| Benchmark | Old | **New** | Mean tokens/query | p50 latency |
|---|---|---|---|---|
| LoCoMo | 71.4 | **92.5** | ~6,956 | 0.88s |
| LongMemEval | 67.8 | **94.4** | ~6,787 | 1.09s |
| BEAM (1M) | — | **64.1** | ~6,719 | 1.00s |
| BEAM (10M) | — | **48.6** | ~6,914 | 1.05s |

Mem0 frames the value as **token efficiency**: ~6,900 tokens/retrieval vs 25,000+ for full-context. Single-pass retrieval (one call, no agentic loops). Scores carry a self-declared ±1 point CI "due to judge inconsistency." ⚠️ These are **vendor self-reported**; the OSS SDK "should expect directionally similar gains but not identical numbers."

### Where the new algorithm is weakest (their own numbers — directly relevant to us)

| Weak category | 1M | 10M |
|---|---|---|
| **contradiction_resolution** | **35.7** | **32.5** |
| event_ordering | 53.6 | 20.2 |
| temporal_reasoning | 61.8 | 16.3 |
| multi_session_reasoning | 65.2 | 26.1 |
| summarization | 63.5 | 46.9 |

Mem0's own docs admit: *"Knowledge update (93.6) remains the hardest category for an additive, ADD-only architecture: older facts are preserved rather than overwritten, so semantically similar prior facts can still surface alongside newer ones."* And: *"Weaker categories at 10M (temporal reasoning, event ordering, multi-session reasoning) are open problems across the field. They require higher-order representations of how events relate to each other across time, which is a primary focus of our ongoing research."*

### Graph Memory — what it actually is (critical)

Verbatim from docs: *"Graph Memory captures which entities your memories are about and how they connect through shared context. It does not assign typed, labeled relationships between entities (it won't, for example, record a 'manages' edge from one person to another); connections are inferred from co-occurrence rather than declared. This is what makes it schema-free and zero-configuration."*

- **Nodes:** entities (people, places, orgs, concepts) + memory (fact) nodes
- **Edges:** entity ↔ every memory mentioning it. Two entities are "related" only if they co-occur in a memory.
- **Effect:** the graph **"affects ranking, not the response shape"** — there is no separate graph payload; entity matches fold into the combined retrieval score. The standalone `relations` field is now always `[]`.

### Dream — memory consolidation (new, 2026)

| Action | What it does | Runs | Availability |
|---|---|---|---|
| **Supersede** | Marks an older fact as outdated when a newer one contradicts it; links old → new. Not deleted, not hidden by default; returned badged "superseded" | On add | Always on, all plans |
| **Merge** | Folds a duplicate into a single canonical memory; merged record hidden by default (`include_merged=true` to see) | On add | Always on, all plans |
| **Synthesis** | Distills higher-order **pattern memories** ("this user is an early riser") with **links back to the source memories it was distilled from** | On a schedule | Opt-in, Pro+ |

Read flags: default returns active + superseded; `latest_only=true` returns "the current truth" only.

### Memory types & categorization
User / Session / Agent scoping (`user_id`, `agent_id`, `app_id`, `run_id`); temporal memory types (event, state, plan, preference, relationship, absence); custom categories.

### Defaults & tech stack
Default LLM: `gpt-5-mini` (OpenAI). Default embeddings: `text-embedding-3-small`. Hybrid search requires `pip install mem0ai[nlp]` + spaCy `en_core_web_sm`; recommends ≥ Qwen 600M-class embeddings for entity/hybrid quality. Supports many LLM providers (per docs "Supported LLMs").

### Notable gaps (product-level)
- **No typed relationship model** — explicitly co-occurrence only; no ontology to define
- **No explicit fact validity intervals** — temporal reasoning is a retrieval *boost* + write-time metadata, not `valid_from`/`valid_to` edges; no "what did the agent believe on date X?" as-of query
- **No belief propagation / confidence recomputation** exposed — ranking is a fused retrieval score, not a belief (§11 flags the undocumented "confidence" claim)
- **Contradiction resolution is their weakest benchmark** (32.5–35.7)
- **Temporal Reasoning is Platform-only** — "It runs automatically on Mem0 Platform v3. It is not available in the OSS SDK."
- **10M-token scale degrades hard** (BEAM 48.6; temporal 16.3)

🔄 **Draft corrections:** (1) "2-call LLM extraction loop" → now **single-pass, 1 call, ADD-only**. (2) "No temporal model — overwrites facts" → **wrong**; nothing is overwritten, superseded facts are retained, temporal metadata exists, plus memory decay. (3) "No contradiction handling" → **wrong as of 2026** (Dream Supersede) — though it is their weakest measured capability and is pairwise write-time, not a logic layer. (4) "No dedup/consolidation" → **wrong**; Merge + Synthesis. (5) "No ontology — flat key-value memory" → **still essentially true** (schema-free, no typed relations), so keep that part.

[Source](https://docs.mem0.ai/core-concepts/memory-evaluation) — retrieved 2026-09-11; [Source](https://docs.mem0.ai/open-source/features/graph-memory) — retrieved 2026-09-11; [Source](https://docs.mem0.ai/platform/features/temporal-reasoning) — retrieved 2026-09-11; [Source](https://docs.mem0.ai/platform/features/dream) — retrieved 2026-09-11; [Source](https://github.com/mem0ai/mem0) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 7. Go-to-Market & Acquisition

| Channel | Activity |
|---|---|
| **Open-source funnel** | Apache-2.0 repo, 65K stars → Platform conversion. Free Hobby tier as top-of-funnel |
| **Framework integrations** | ~25 documented integrations (LangChain, LangGraph, LlamaIndex, CrewAI, AutoGen, Agno, OpenAI Agents SDK, Google ADK, Vercel AI SDK, Mastra, Strands, LiveKit, Pipecat, ElevenLabs, Bedrock, Dify, Flowise, n8n, Zapier, Raycast…) |
| **Native embeds** | CrewAI, Flowise, Langflow integrate Mem0 natively (distribution into other products' user bases) |
| **Cloud partnerships** | AWS: **"AWS selected us as the exclusive memory provider for their new Agent SDK"**; AWS blog + docs feature Mem0 with Bedrock, AgentCore Runtime, ElastiCache Valkey, Neptune Analytics |
| **Agent-native onboarding** | `mem0 init --agent` — agents self-provision keys in <5s |
| **Agent skills/plugins** | `npx skills add … --skill mem0-integrate` pipeline; Claude Code / Codex / Cursor / Windsurf / OpenCode plugins |
| **Content/SEO engine** | High-cadence blog (multiple posts/week), research pages, benchmark deep-dives, comparison pages, per-vertical use-case pages |
| **Devrel / research** | arXiv paper (2504.19413), open-sourced benchmark harness (`mem0ai/memory-benchmarks`), 486+ research citations |
| **Investor network** | YC + angels who are infra CEOs (Datadog, Supabase, PostHog, W&B, ex-GitHub) — distribution + credibility loop |

**Sales motion:** Product-led/open-source self-serve (Hobby/Starter/Pro, card-on-file) → enterprise sales (on-prem, SSO, SLA, audit logs). Investor-angels double as design partners/credibility.

**Marketplace:** No App Store / marketplace presence — dev-infra motion, not B2C.

⚠️ **Gap:** Mem0's own comparison/alternative-page inventory was not enumerable from a single page (they have many). Not quantified here — would need a site crawl.

[Source](https://mem0.ai/series-a), [Source](https://docs.mem0.ai/integrations), [Source](https://mem0.ai/) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 8. Traction & Scale

| Signal | Value | Source / date |
|---|---|---|
| GitHub stars | **65.1K** (live repo) | github.com/mem0ai/mem0 — 2026-09-11 |
| GitHub forks | **7.6K** | same |
| Open issues / PRs | 302 issues / 433 PRs | same |
| GitHub stars (homepage widget) | 62,590 ⚠️ stale vs live repo | mem0.ai — 2026-09-11 |
| GitHub stars (About page) | "58,000+" ⚠️ stale | mem0.ai/about-us — 2026-09-11 |
| Python package downloads | **14M+** | mem0.ai/series-a — Oct 2025 |
| Python package downloads | 13M+ | TechCrunch — 2025-10-28 |
| Developer signups | **160,000+ developers** (About); 150,000+ (homepage); "80,000+ cloud signups" (TechCrunch Oct 2025) | various — 2026-09-11 |
| API calls | **35M (Q1 2025) → 186M (Q3 2025)** | mem0.ai/series-a |
| Funding | **$24M** across Seed + Series A | PR Newswire / mem0.ai/series-a |
| Research citations | 486+ | mem0.ai/about-us |
| Named customer | **Trend Micro** (Bedrock + Neptune company memory) | AWS ML blog |
| Native product embeds | CrewAI, Flowise, Langflow | mem0.ai/series-a |
| Cloud partner | AWS — "exclusive memory provider for their new Agent SDK" | mem0.ai/series-a |
| Customer breadth | "Thousands of teams… to Fortune 500 companies" (unnamed) | mem0.ai/series-a |
| ⚠️ Public revenue / ARR | **None disclosed.** No pricing-page revenue, no ARR figure found | — |

**Growth signal:** API calls grew ~5.3× from Q1→Q3 2025 while GitHub stars roughly doubled from 41K (Oct 2025) to 65K (Sep 2026). The trajectory is real and steep — this is the category's distribution leader by OSS metrics.

[Source](https://github.com/mem0ai/mem0), [Source](https://mem0.ai/series-a), [Source](https://techcrunch.com/2025/10/28/mem0-raises-24m-from-yc-peak-xv-and-basis-set-to-build-the-memory-layer-for-ai-apps/), [Source](https://mem0.ai/about-us) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 9. SEO & Organic Presence

| Metric | Value |
|---|---|
| ⚠️ Estimated monthly traffic | Not in public indices — requires SimilarWeb/Ahrefs (paid) |
| ⚠️ Domain Authority / DR | No public data |
| Core keyword territory | "AI memory", "AI agent memory", "memory layer", "LLM memory", "persistent memory for AI agents", "agent memory benchmark", "LongMemEval", "LoCoMo", "BEAM benchmark" |
| Content volume | High — multiple blog posts/week (e.g. Sep 10 & Sep 11, 2026 posts observed), dedicated research section, benchmark deep-dive pages, per-vertical use-case pages (Healthcare, Education, Sales & CRM, E-Commerce, Customer Support) |
| Technical content assets | `/research` benchmark hub, `/blog`, docs portal, open-source benchmark repo, arXiv paper, Trust Center, Status page |
| Comparison posture | Publishes vendor comparisons ("State of AI Agent Memory 2026", benchmark vs. Zep/OpenAI/LangMem/MemGPT) — content-led category definition |

**Keyword overlap with Tortoise:** Partial and important. Overlap on head terms ("AI agent memory", "LLM memory", "knowledge graph memory") and on benchmark terms ("LongMemEval", "contradiction resolution" via BEAM). Tortoise's differentiated terms ("epistemic memory", "belief propagation", "NAND / contradiction", "knowledge graph confidence", "temporal supersession") are **not** owned by Mem0 — that's the exploitable SEO and category-definition gap.

⚠️ Traffic/DA figures require paid tooling — documented gap, not fabricated.

[Source](https://mem0.ai/), [Source](https://mem0.ai/research) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 10. Social & Community

| Channel | Followers / Members | Engagement notes |
|---|---|---|
| Discord | ⚠️ Exists (linked in README + docs) — **member count not publicly retrievable without joining** | Primary community channel |
| X/Twitter (@mem0ai) | ⚠️ ~20.1K (bio: "Memory Layer for your AI agents") — single-source, count ambiguous in snippet | Active; company product announcements |
| GitHub org | 65.1K stars on main repo | Strongest signal |
| LinkedIn | ⚠️ Not located / not verified in this pass | Gap |
| GitHub Discussions | Active (linked from repo nav) | Q&A + roadmap |
| YouTube / demo | Repo links a demo | — |

**Community mechanics:**
- **OSS → Platform flywheel:** free library, Discord support, community tier on pricing page
- **Agent-plugin distribution:** Mem0 ships Claude Code / Codex / Cursor / Windsurf / OpenCode / Kimi plugins + a skills catalog, meeting developers inside their coding assistants
- **Agent-native signup:** `mem0 init --agent` lowers the join cost for autonomous agents
- **Research as community artifact:** open-sourced benchmark harness lets the community re-run and audit their numbers — a trust-building mechanic that also invites the exact criticism in §11
- **Sustained release cadence:** 2,626 commits; daily blog cadence

⚠️ **Gaps:** Discord member count, LinkedIn follower count, and X follower count could not be verified from a live page in this pass (Discord requires join; X/LinkedIn blocked to fetch).

[Source](https://github.com/mem0ai/mem0) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 11. Customer Sentiment

**Sources checked:** Zep's benchmark rebuttal blog, Atlan "Mem0 alternatives" (third-party, competitor-adjacent), Medium hands-on critique, askonoma review, Mem0 docs self-admissions, GitHub repo (issues/PR counts).

**What users/developers praise:**
- **Time-to-value:** 3-line drop-in API; broadest framework integration matrix in the category (~25 integrations)
- **Token efficiency:** ~6.9K tokens/retrieval vs 25K+ full-context — directly costs less per query
- **History preservation:** ADD-only means nothing is silently destroyed; superseded facts are retained and badged, not deleted
- **Distribution/credibility:** 65K stars, 14M PyPI downloads, AWS "exclusive memory provider for the Agent SDK", $24M from top infra investors
- **Release velocity:** algorithm rewritten and re-benchmarked within a year (LoCoMo 71.4→92.5; LongMemEval 67.8→94.4)

**What users/developers complain about:**
- **Pricing cliff for graph/consolidation:** the $19 → $249 jump for Pro-tier graph/analytics is described as *"the most-cited community frustration across developer forums and Hacker News discussions"* and *"the top reason developers switch away from Mem0"* (Atlan, Apr 2026) ⚠️ third-party source
- **Benchmark credibility dispute:** Zep's public rebuttal *"Lies, Damn Lies, & Statistics: Is Mem0 Really SOTA in Agent Memory?"* (May 2025, updated Jun 2026) claims Mem0's LoCoMo comparison mis-implemented Zep (wrong user model, timestamps appended to messages, sequential searches) and that "a simple full-context baseline" beat Mem0's best score (~73% vs ~68%); Zep reports 75.14% ± 0.17 when correctly implemented. It also calls LoCoMo itself a flawed benchmark (unusable category 5, multimodal errors, wrong speaker attribution) ⚠️ competitor-authored rebuttal — but the critique points are specific and falsifiable
- **Self-reported vs. competitor-run gap:** Mem0 self-reports **94.4%** on LongMemEval; a competitor-run comparison (vectorize.io — Vectorize AI Inc. is the vendor of Hindsight, on its own comparison page) reports **49.0%** (vs Zep 63.8%, GPT-4o) — a ~45-point discrepancy. Mem0 disputes the competitor-run number. ⚠️ The **~26%** figure cited in the research brief could NOT be traced to a third-party comparison; the only verifiable "26%" is Mem0's own paper claim of *"26% relative improvements in the LLM-as-a-Judge metric over OpenAI"* on LoCoMo (arXiv 2504.19413). Treat 26% as an unverified/conflated figure — **do not repeat it as an independent benchmark result.**
- **Temporal retrieval gap:** third-party analyses attribute the 49% result to vector-similarity retrieval lacking fact-validity intervals — *"Temporal queries require knowing when a fact was valid, not just what it currently says"* (Atlan) ⚠️ third-party
- **No implicit pattern learning:** a Feb 2026 HN thread (per Atlan) documented that Mem0 stores explicit facts but does not infer behavioral patterns from repeated interactions; independent benchmarking put implicit-preference accuracy at 30–45% vs 77–90% for long-context approaches ⚠️ single-source (Atlan), unverified independently
- **Hands-on recall failure:** a Medium "AI Memory Challenge" author reports Mem0 failed all five targeted recall/reasoning questions, arguing its summarization-based approach is weak for precise retrieval ⚠️ single-author, not reproduced
- **Operational complexity:** reviews cite technical complexity, infrastructure overhead, limited out-of-box features, and scaling challenges as memory grows (askonoma review) ⚠️ single-source
- **Security:** Atlan references a **CVE-2026-0994 protobuf issue** affecting Mem0 ⚠️ **unverified in this pass** — no NVD/GitHub advisory confirmed; do not repeat as fact
- **Self-admitted accuracy weakness:** their own docs concede knowledge update and contradiction resolution are the hardest categories for an ADD-only architecture (BEAM `contradiction_resolution` 35.7 / 32.5)

**Overall sentiment:** **Mixed.** Best-in-class distribution and developer experience, but a persistent and substantive accuracy-credibility dispute plus a documented pricing-cliff complaint. The gap between vendor benchmarks (94.4) and a competitor's own comparison (49.0) is the single most important sentiment signal for us — it means Mem0's *claimed* moat is contested and its *temporal/contradiction* capabilities are the acknowledged weak spot.

[Source](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/) — retrieved 2026-09-11; [Source](https://atlan.com/know/mem0-alternatives/) — retrieved 2026-09-11; [Source](https://vectorize.io/articles/mem0-vs-zep) — retrieved 2026-09-11 (via search); [Source](https://arxiv.org/abs/2504.19413) — retrieved 2026-09-11; [Source](https://docs.mem0.ai/core-concepts/memory-evaluation) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## Notes & Sources

### Primary sources fetched (live, 2026-09-11)
- **Homepage:** [mem0.ai](https://mem0.ai/) — tagline, 150,000+ developers, 62,590 star widget, compliance badges (SOC 2 Type I / HIPAA / GDPR)
- **Pricing:** [mem0.ai/pricing](https://mem0.ai/pricing) — full tier table (Hobby $0 / Starter $19 / Pro $249 / Enterprise custom), add-vs-retrieval request metering
- **About:** [mem0.ai/about-us](https://mem0.ai/about-us) — 160,000+ developers, $24M raised, 58,000+ stars, 486+ citations, founder bios
- **Funding announcement:** [mem0.ai/series-a](https://mem0.ai/series-a) — $24M across Seed+Series A, investor list, API-call growth (35M Q1 → 186M Q3), AWS Agent SDK exclusive, CrewAI/Flowise/Langflow native
- **Research hub:** [mem0.ai/research](https://mem0.ai/research) — LoCoMo 92.5, LongMemEval 94.4, BEAM 64.1/48.6, token-efficiency framing
- **Docs — evaluation:** [docs.mem0.ai/core-concepts/memory-evaluation](https://docs.mem0.ai/core-concepts/memory-evaluation) — full benchmark tables incl. BEAM `contradiction_resolution` 35.7/32.5; self-admitted ADD-only weakness
- **Docs — graph memory:** [docs.mem0.ai/open-source/features/graph-memory](https://docs.mem0.ai/open-source/features/graph-memory) — co-occurrence-only, no typed relations, external graph store replaced
- **Docs — temporal reasoning:** [docs.mem0.ai/platform/features/temporal-reasoning](https://docs.mem0.ai/platform/features/temporal-reasoning) — ranking boost, `reference_date`, Platform-only (not OSS)
- **Docs — Dream:** [docs.mem0.ai/platform/features/dream](https://docs.mem0.ai/platform/features/dream) — Supersede / Merge / Synthesis, plan gating
- **Docs — integrations:** [docs.mem0.ai/integrations](https://docs.mem0.ai/integrations) — ~25 frameworks/tools
- **GitHub:** [github.com/mem0ai/mem0](https://github.com/mem0ai/mem0) — 65.1K stars, 7.6K forks, 302 issues, 433 PRs, 2,626 commits; README benchmark table + algorithm change log
- **arXiv:** [2504.19413](https://arxiv.org/abs/2504.19413) — "Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory" (submitted 2025-04-28); claims 26% relative improvement over OpenAI on LLM-as-a-Judge; graph variant ~2% above base; 91% lower p95 latency, 90%+ token savings

### Secondary / third-party sources
- **Y Combinator:** [ycombinator.com/companies/mem0](https://www.ycombinator.com/companies/mem0) — founded 2023, SF, 10 employees, $24M
- **TechCrunch (2025-10-28):** [mem0-raises-24m…](https://techcrunch.com/2025/10/28/mem0-raises-24m-from-yc-peak-xv-and-basis-set-to-build-the-memory-layer-for-ai-apps/) — 41K stars, 13M+ Python downloads, 80,000+ cloud signups
- **PR Newswire:** $24M led by Basis Set Ventures, with Peak XV, GitHub Fund, Y Combinator
- **Zep rebuttal (2025-05-06, upd. 2026-06-03):** [blog.getzep.com/lies-damn-lies…](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/) — competitor-authored benchmark critique; specific implementation-error claims
- **Vectorize (competitor-run comparison — Vectorize AI Inc. is the vendor of Hindsight, a Tier-1 competitor, so this is not an independent evaluation):** [vectorize.io/articles/mem0-vs-zep](https://vectorize.io/articles/mem0-vs-zep) — LongMemEval 49.0% Mem0 vs 63.8% Zep (GPT-4o)
- **Atlan "Mem0 alternatives" (2026-04-08):** [atlan.com/know/mem0-alternatives](https://atlan.com/know/mem0-alternatives/) ⚠️ competitor-adjacent marketing page — pricing-cliff complaint, temporal gap analysis, HN Feb 2026 implicit-pattern thread, CVE-2026-0994 reference
- **Medium "AI Memory Challenge Pt.1":** hands-on critique, reports 5/5 recall failures ⚠️ single author
- **askonoma Mem0 review:** complexity/scaling complaints ⚠️ single source
- **Inc42 company profile:** 21 employees ⚠️ conflicts with YC's 10
- **Forecast Desk tracker:** ⚠️ "59k+ stars, 186M API calls (Q3)" — third-party, aspirational caching

### Documented gaps (NOT fabricated)
- ⚠️ **Discord member count** — requires joining; not retrieved
- ⚠️ **LinkedIn follower count** — not verified in this pass
- ⚠️ **X/Twitter follower count** — ~20.1K reported by search, snippet ambiguous; unverified against live profile
- ⚠️ **ARR / revenue** — no public figure; no revenue estimate found from a credible source
- ⚠️ **SEO traffic / domain authority** — requires paid SimilarWeb/Ahrefs
- ⚠️ **CVE-2026-0994** — referenced by Atlan but not confirmed against NVD or a GitHub advisory; treat as unverified
- ⚠️ **The "~26%" LongMemEval third-party figure** — not located; the only verified 26% is Mem0's own LoCoMo relative-improvement claim vs OpenAI. Likely a conflation.
- ⚠️ **"Fortune 500 customers"** — claimed, unnamed; only one named enterprise case study found (Trend Micro)
- ⚠️ **Graph-memory plan gating** — Mem0's own pricing page and docs contradict each other (documented in §5)

### 🔄 Draft corrections (what the prior stub got wrong)
1. **"Free tier: 1000 memories. Pro: usage-based."** → Wrong. Free = 10,000 add + 1,000 retrieval **requests**/mo; $19 Starter; $249 Pro; usage-based is a separate sales path.
2. **"Vector store + optional graph (Pro tier)"** → Outdated. Graph Memory is native and always-on (co-occurrence entity linking); the old external graph-store integration (Neo4j/Memgraph/Kuzu/AGE/Neptune) was **replaced**. Only the Graph *view* + Dream *Synthesis* are Pro+ per docs (pricing page is inconsistent).
3. **"2-call LLM extraction loop (extract facts → store)"** → Outdated. April 2026 algorithm: **single-pass, ADD-only extraction** (one call, no UPDATE/DELETE).
4. **"No temporal model — overwrites facts rather than versioning them"** → Wrong on both halves. Nothing is overwritten (ADD-only, history retained); Temporal Reasoning metadata + `reference_date` + `latest_only` + memory decay now exist. (Still no explicit `valid_from`/`valid_to` graph intervals.)
5. **"No contradiction handling"** → Wrong as of 2026: Dream Supersede marks contradicted facts outdated and links old→new. But contradiction resolution remains their weakest benchmarked category (32.5–35.7), and it is pairwise write-time detection, not a logic/edge layer.
6. **"No dedup/consolidation"** → Wrong: Dream Merge (dedup) + Synthesis (higher-order pattern memories with provenance links to source memories).
7. **"No ontology — flat key-value memory"** → Still accurate. Schema-free; docs explicitly state there are **no typed, labeled relationships between entities** — connections are co-occurrence only.
8. **"49% LongMemEval on the graph variant"** → Misattributed. 49.0% is a **competitor-run** number (vectorize.io — Vectorize AI Inc., the vendor of Hindsight, on its own comparison page), not an independent evaluation; Mem0 self-reports 94.4% (new) / 67.8% (old). The *graph variant* was only ~2% above base on LoCoMo in the 2025 paper.
9. **"No belief propagation — no mechanism for 'why should I believe this?'"** → Still true, with nuance: the Series A post claims Mem0 layers in "metrics like decay and **confidence**," but no public docs define, expose, or recompute a confidence/belief. ⚠️ single-source (company blog) — treat as an undocumented internal score, not a belief-propagation engine.
10. **"User-centric — designed for personalization, not organizational knowledge"** → Partially outdated. Entity scoping (`agent_id`/`app_id`/`run_id`) + company-memory use cases (Trend Micro/Bedrock) push into org knowledge; but Synthesis explicitly excludes memories carrying non-user entities, and the model remains user-fact-centric. Directionally still valid.

*Last updated: 2026-09-11*
