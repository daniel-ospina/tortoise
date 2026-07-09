# Hindsight (Vectorize)

> Open-source agent memory system that learns. "Agent Memory That Learns" — not RAG, not a vector DB wrapper.

---

## 1. Overview

| Field | Value |
|---|---|
| Founded | 2024 |
| HQ | Boulder, Colorado (registered: Dover, DE) |
| Funding raised | $3.6M Seed (Oct 2024), led by True Ventures |
| Team size | 2–10 employees (⚠️ estimated) |
| Markets | Global (AI developers, agent platforms, enterprise) |
| Key milestone | Hindsight launched Dec 2025; 18K GitHub stars by mid-2026; LongMemEval SOTA |

*Last checked: 2026-07-06*

---

## 2. Product Type

**Hindsight** — Open-source agent memory system. Three core operations: **Retain** (store structured memories) → **Recall** (4-way hybrid search) → **Reflect** (AI reasons over memories, builds beliefs). Four memory types: World Facts, Experience Facts, Observations (auto-consolidated), Mental Models (user-curated).

**Vectorize** — Original RAG platform (pipeline builder, evaluation tools, DB connectors). Still live at `docs.vectorize.io` but Hindsight is the breakout product.

**Company structure:** Vectorize AI, Inc. is the company. Hindsight is the flagship product. The Vectorize RAG platform appears to be the predecessor.

⚠️ `vectorize.io` homepage returned fetch error (2026-07-06). Hindsight docs at `hindsight.vectorize.io` are live and comprehensive.

*Last checked: 2026-07-06*

---

## 3. Positioning & Messaging

**Tagline:** "Agent Memory That Learns"

**Value proposition (their words):** "Simple vector search isn't enough — 'What did Alice do last spring?' requires temporal reasoning, not just semantic similarity."

**CEO statement (verbatim):** "RAG is effectively dead, and I believe agent memory is what will ultimately eliminate it." — Chris Latimer, CEO

**Brand voice:** Developer-centric, benchmark-driven, technically rigorous. Aggressively anti-RAG positioning. "MIT open source, no feature walls." "Not a vector DB wrapper — a different paradigm."

⚠️ Source: Perplexity + docs. `vectorize.io` homepage unretrievable.

*Last checked: 2026-07-06*

---

## 4. Target Audience

**Primary:** AI application developers building agents that need persistent, cross-session memory.

| Segment | Details |
|---------|---------|
| Personas | AI app developers, agent platform builders, MLOps engineers |
| Use cases | Customer support agents, personal AI assistants, multi-platform memory (Slack, Discord, Teams, Google Chat, GitHub, Linear — same memory bank) |
| Stack | Python/TS/Go SDKs, Docker/Helm/pip deploy, PostgreSQL 14+, any OpenAI-compatible embeddings API |
| Integrations | 59 (51 official, 6 community, 2 cookbook): LangChain, CrewAI, AutoGen, Claude SDK, Cursor, Copilot, Google ADK, Microsoft Agent Framework, n8n, Dify, Vercel AI SDK, and more |
| Early customer | Groq (⚠️ single-source, unverified) |

*Last checked: 2026-07-06*

---

## 5. Business Model & Pricing

**Revenue model:** Open-core — self-hosted OSS (MIT, free, no limits) → Hindsight Cloud (pay-as-you-go per M tokens, SOC 2 managed).

### Cloud Token Pricing

✅ Pricing verified against live page at `vectorize.io/pricing` (retrieved 2026-07-06). Note: `hindsight.vectorize.io/pricing` returns 404 — pricing lives on the company domain, not the docs domain.

| Operation | $/M tokens |
|-----------|-----------|
| Retain (store memories) | $10.00 |
| Recall (search) | $0.75 |
| Reflect (reason over memories) | $0.05/call |
| Iris Extract | $7.50 |
| Mental Model Retrieve | $0.25 |
| Mental Model Refresh | $0.05/call |
| Storage | $0.25/M tokens/month (free first 30 days) |

Volume discounts available. No monthly fees, no seat pricing. Credit packages: $10, $25, $50, $100 standard (custom $5–$1,000 via Stripe). Enterprise: Net 30/60 invoiced.

🔄 **Price reduction observed:** Retain dropped from $15/M → $10/M since earlier research. Reflect and Mental Model Refresh moved from per-token to per-call pricing ($0.05/call). Competitive pressure signal.

**Open source:** MIT license, no telemetry, no feature walls, Docker one-command deploy. Same codebase for OSS and Cloud.

✅ Pricing verified against live page. [Source](https://vectorize.io/pricing) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 6. Product & Features

### Architecture

| Component | Detail |
|-----------|--------|
| **Database** | PostgreSQL 14+ with pgvector/pgvectorscale/vchord/scann |
| **Embeddings** | Any OpenAI-compatible API + LiteLLM |
| **Deploy** | Docker, Kubernetes/Helm, pip (embedded mode), bare metal |
| **API surface** | REST (port 8888), Web UI (port 9999), MCP Server (`/mcp`) |
| **SDKs** | Python, TypeScript, Go, CLI, HTTP |

### TEMPR Retrieval Engine — 4 parallel strategies

| Strategy | What it finds | Example |
|----------|--------------|---------|
| **Dense vector** (semantic) | Paraphrasing, conceptual similarity | "prefers cycling" ≈ "enjoys biking" |
| **Sparse/BM25** (keyword) | Names, technical terms, exact matches | "React 19 migration" |
| **Graph traversal** | Entity relationships, indirect connections, multi-hop | "Alice → works at → Acme → partnered with → BetaCorp" |
| **Temporal search** | Time-aware causal chains | "last spring", "in June", "before the launch" |

Merged via Reciprocal Rank Fusion (RRF) + cross-encoder reranker. Token-budget optimization (not top-K).

### Memory Types

| Type | Description | Examples |
|------|-------------|----------|
| **World Facts** | Facts about entities | "Alice is CEO of Acme" |
| **Experience Facts** | Event-attributed facts | "Alice said she prefers dark mode on June 5" |
| **Observations** | Auto-consolidated patterns | "Alice upgrades within 2 weeks of every launch" |
| **Mental Models** | User-curated beliefs | "Alice is a power user who values speed over features" |

### Observation Engine

Auto-dedup, evidence tracking (exact quotes + proof count), continuous refinement (update, not overwrite), freshness awareness (stale observations verified before use).

### Memory Bank Configuration

- **Mission** — agent identity
- **Directives** — hard rules
- **Disposition** — skepticism/literalism/empathy (scale 1–5)

### Benchmarks

| Benchmark | Hindsight Score | Competitors |
|-----------|----------------|-------------|
| LongMemEval | **91.4%** (Gemini-3) / **94.6%** (top reproduced) | Mem0: 49.0%, Zep: 71.2%, GPT-4o: 60.2% |
| LoCoMo | **89.61%** | Prior best open: 75.78% |
| BEAM (10M tokens) | **64.1%** | 58% ahead of Honcho |

+44.6 points over full-context baseline on LongMemEval.

⚠️ Benchmark methodology not independently verified beyond what's published in their docs.

[Source](https://hindsight.vectorize.io/), [GitHub](https://github.com/vectorize-io/hindsight) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 7. Go-to-Market & Acquisition

**Primary growth channels:**

| Channel | Activity |
|---------|----------|
| **GitHub OSS** | Primary engine. 18K stars, 10K+ gained in ~7 months (~150/day peak), trending #4 (Mar 2026) |
| **Benchmark leadership** | LongMemEval SOTA, independently validated by Virginia Tech + Washington Post |
| **Integration ecosystem** | 59 integrations (51 official + 6 community + 2 cookbook) — "be the default memory for every agent" (51 official: LangChain, CrewAI, AutoGen, Claude SDK, Cursor, Copilot, etc.; 6 community; 2 cookbook) |
| **Content** | Blog, cookbook, changelog. High-quality Docusaurus docs. |
| **Community** | GitHub Discussions, Slack, "Show and Tell" sessions |

**Sales motion:** Self-serve OSS adoption → Cloud upgrade. Enterprise: SOC 2 + volume discounts.

⚠️ No data on paid acquisition, conference presence, or DevRel team.

*Last checked: 2026-07-06*

---

## 8. Traction & Scale

| Signal | Value |
|---|---|
| GitHub stars | ~18,000 (peak velocity ~150/day)
| Cloud sign-ups | 1,000+ (Apr 2026) |
| GitHub issues | 1,680+ (May 2026) |
| GitHub contributors | 12 (core repo) / 134 (total) |
| Website traffic | ~48K visits/3mo, +48% growth (⚠️ single-source estimate) |
| Avg visit duration | 2m 16s |
| Funding | $3.6M Seed (True Ventures) |
| Product Hunt | 3 reviews, 5.0/5.0, 1.3K followers |
| G2 (RAG platform) | 7 reviews, 4.5/5.0 |
| ⚠️ Revenue / ARR | No public data — likely pre-revenue at seed stage |
| ⚠️ Named customers | Groq (unverified single-source) |
| Press / notable mentions | Virginia Tech + Washington Post benchmark validation; trending #4 on GitHub |

[Source](https://github.com/vectorize-io/hindsight), [Trendshift](https://trendshift.io), [ProductHunt](https://producthunt.com) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 9. Online Presence & Content

| Metric | Value |
|---|---|
| ⚠️ Main website | `vectorize.io` — fetch failed (2026-07-06) |
| Hindsight docs | `hindsight.vectorize.io` — ✅ live, excellent Docusaurus |
| RAG platform docs | `docs.vectorize.io` — ✅ live |
| GitHub org | `github.com/vectorize-io` — 26 repos |
| ⚠️ Estimated traffic | ~48K visits/3mo (single-source) |
| Content strategy | Benchmark leadership + integration breadth + high-quality docs |

**Docs quality:** Excellent — clear hierarchy, 60-second quick start, code examples in 3 languages, integration hub with individual pages, benchmark paper link.

⚠️ Traffic estimate is single-source and unverified. DA/DR not available in public indices.

*Last checked: 2026-07-06*

---

## 10. Community & Ecosystem

| Channel | Followers / Members | Engagement notes |
|---|---|---|
| GitHub | 18,000 stars | 1,680+ issues, active Discussions, "Show and Tell" |
| Slack | Mentioned in docs footer | ⚠️ Size unknown |
| Product Hunt | 1,300 followers | 3 reviews, 5.0/5.0 |
| Reddit | r/AI_Agents discussion | "sound, robust architecture" |
| ⚠️ Twitter/X | Founder accounts found | No company handle surfaced |
| ⚠️ Discord | Not found | Docs mention Slack, not Discord |

**Platform strategy:** Be the memory layer for every agent framework. Not competing with frameworks — integrating with all 40+ of them.

**Community dynamics:** Fast-growing OSS community (10K stars in 4.5 months). High issue volume (1,680+) suggests active usage. Core contributors are small (12) but total contributor base is wide (134).

[Source](https://github.com/vectorize-io/hindsight) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 11. Customer Sentiment

**Sources checked:** Product Hunt, G2, Reddit (r/AI_Agents), GitHub Discussions

**What users/customers praise:**
- Benchmark credibility — independently verified by Virginia Tech, Washington Post
- Developer experience — "Polished, dependable" (Product Hunt), "effortless" (G2), 60-second quick start
- Production-ready — SOC 2, PostgreSQL, Docker one-command, no feature walls between OSS and Cloud
- Same codebase for OSS and Cloud — no crippleware
- "Standout support" from the team (Product Hunt)

**What users/customers complain about:**
- API stability concerns for multi-agent setups (Reddit)
- UI bugs reported on G2 (likely RAG platform, not Hindsight)
- Pricing opacity — pricing page returns 404
- Maturity concerns — <1 year old product, <2 year old company, 2–10 employees
- Bus factor — two founders are the core team

**Overall sentiment:** Strongly positive, early stage. Strong developer love, weak enterprise proof points (no named customers beyond unverified Groq, no revenue data).

[Source](https://producthunt.com), [G2](https://g2.com), [Reddit](https://reddit.com/r/AI_Agents) — retrieved 2026-07-06

*Last updated: 2026-07-06*

---

## Notes & Sources

- **Primary:** [hindsight.vectorize.io](https://hindsight.vectorize.io/) — Hindsight docs, retrieved 2026-07-06
- **GitHub:** [github.com/vectorize-io/hindsight](https://github.com/vectorize-io/hindsight) — 18K stars, MIT
- **⚠️ vectorize.io homepage** — fetch failed (2026-07-06). Company/product data from Perplexity + docs.
- **✅ Pricing page** — `hindsight.vectorize.io/pricing` returns 404, but pricing lives on `vectorize.io/pricing`. Verified against live page 2026-07-06. Price reduction detected: Retain $15→$10/M, Reflect + Mental Model Refresh moved to per-call pricing ($0.05/call).
- **⚠️ Groq as customer** — single-source, unverified.
- **⚠️ Traffic data** — single-source estimate, 48K visits/3mo.
- **⚠️ Revenue** — no public data. Seed-stage, likely pre-revenue.

*Last updated: 2026-07-06*
