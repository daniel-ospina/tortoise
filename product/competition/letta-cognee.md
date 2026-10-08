# Letta (MemGPT) & Cognee

> Two developer-adopted open-source agent-memory frameworks frequently cited in the "there is more to memory than RAG" debate — but they sit at opposite ends of the memory-design spectrum. **Letta** is an agent harness whose memory is a git-backed filesystem the LLM edits itself (agent-managed context, no graph). **Cognee** is a graph-building memory engine (extract entities + relationships into a knowledge graph, with opt-in contradiction edges and bi-temporal fact validity).

> **Multi-product profile.** Two companies in one file per the skill's multi-product rule: they compete for the same builder, are cross-migrated (Cognee ships `LettaSource`, `ZepSource`, `GraphitiSource` importers), and are routinely named together in third-party comparisons. Sub-sections are marked per system wherever they differ.

---

## 1. Overview

### Letta

| Field | Value |
|---|---|
| Founded | 2024 (company); MemGPT research paper Oct 2023 |
| HQ | San Francisco, CA |
| Founders | Charles Packer (CEO), Sarah Wooders (CTO) — creators of MemGPT, UC Berkeley |
| Origin | Spun out of UC Berkeley's Sky Computing Lab; advised by Ion Stoica and Joey Gonzalez |
| Funding raised | **$10M Seed** (Sep 2024), led by Felicis (Astasia Myers); $70M post-money valuation. Participants: Sunflower Capital, Essence VC. Angels: Jeff Dean, Clem Delangue, Cris Valenzuela, Jordan Tigani, Tristan Handy, Barry McCardel, Robert Nishihara |
| Team size | 11–50 (LinkedIn); 16 employees (Tracxn, as of 2026-04-30) |
| Markets | Global — developers building stateful/coding agents; also individual "personal agent" users |
| Key milestones | MemGPT (Oct 2023) → out of stealth with $10M (Sep 2024) → Sleep-time Compute (Apr 2025) → Context Repositories, git-based memory (Feb 2026) → Letta Code launched (⚠️ Apr 6, 2026 — single-source) → Context Constitution (Apr 2026) → Memory Models (Jun 2026) |

[Source](https://www.letta.com/) — retrieved 2026-09-11 · [TechCrunch](https://techcrunch.com/2024/09/23/letta-one-of-uc-berkeleys-most-anticipated-ai-startups-has-just-come-out-of-stealth/) — retrieved 2026-09-11 · [PR Newswire](https://www.prnewswire.com/news-releases/berkeley-ai-research-lab-spinout-letta-raises-10m-seed-financing-led-by-felicis-to-build-ai-with-memory-302257004.html) — retrieved 2026-09-11 · [LinkedIn](https://www.linkedin.com/company/letta-ai) — retrieved 2026-09-11 · [Tracxn](https://tracxn.com/d/companies/letta/__U0QCJ7iCo3pX97e44fpKmKTCiUKKF6Ki8rWRAUaHxHY) — retrieved 2026-09-11

⚠️ No Series A announced as of 2026-09-11 — the only round on record remains the Sep 2024 $10M seed.
⚠️ Team size conflicts across sources: LinkedIn band 11–50 vs Tracxn point-in-time 16. Reported as a range.

### Cognee

| Field | Value |
|---|---|
| Founded | 2024 (company, Berlin); GitHub project created 2023-08-16 |
| HQ | Berlin, Germany — Schönhauser Allee 163 |
| Legal entity | Topoteretes UG (haftungsbeschränkt), Amtsgericht Charlottenburg HRB 252065B; Managing Director: Vasilije Markovic |
| Founders | Vasilije Markovic (CEO), Boris Arzentar |
| Funding raised | **$7.5M Seed** (Feb 2026), led by Pebblebed, with 42CAP and Vermilion Ventures; ~$9M total across 2 rounds (Caplight $9M / Seedtable $8.9M / TheCompanyCheck $9.09M) |
| Team size | Not publicly disclosed — Berlin team across engineering, design, product, commercial roles |
| Markets | Global — developers (OSS), agent builders, enterprises (BYOC) |
| Key milestones | Cognee 1.0 launch → $7.5M seed (Feb 2026) → BEAM SOTA claim (0.79 @ 100K, 0.67 @ 10M) → 30.6K GitHub stars, 5M+ SDK runs/month |

[Source](https://www.cognee.ai/pricing) — retrieved 2026-09-11 · [Cognee seed announcement](https://www.cognee.ai/cognee-raises-seven-million-five-hundred-thousand-dollars-seed) — retrieved 2026-09-11 · [Trending Topics](https://www.trendingtopics.eu/cognee-berlin-startup-raises-7-5-million-builds-long-term-memory-for-ai-agents/) — retrieved 2026-09-11 · [Caplight](https://www.caplight.com/company/cognee) — retrieved 2026-09-11 · [Seedtable](https://www.seedtable.com/startups/Cognee-EW98RKN) — retrieved 2026-09-11

⚠️ Funding total is inconsistent across aggregators ($8.9M–$9.09M). The $7.5M seed itself is confirmed by Cognee's own announcement.
⚠️ Team size not public — no headcount figure found on their site, LinkedIn, or press.

*Last checked: 2026-09-11*

---

## 2. Product Type

### Letta — an agent harness, not a memory API

| Product | Description |
|---|---|
| **Letta Harness / Letta Code** (`letta-ai/letta-code`, Apache-2.0) | Open-source stateful agent harness: CLI + interactive TUI + App Server + channels + runtime used by the desktop/web apps. `npm i -g @letta-ai/letta-code` |
| **Letta Agent SDK** | Wraps the harness so developers can embed stateful agents in their own applications |
| **Letta Desktop / Letta Web** | macOS/Windows/Linux desktop app + `chat.letta.com` |
| **Letta Cloud** | Hosted agents, MemFS repositories, shared-memory repos, cloud sandboxes ("computers") |
| **Legacy** `letta-ai/letta` server | The original MemGPT-style agent server with memory blocks / archival memory. Still on GitHub (24.7K stars) but **no longer the current product**: "The current source code lives in `letta-ai/letta-code`." Last release 0.16.8 (2026-05-14) |

**Category:** Agent harness / stateful-agent platform + agent-managed memory (git-backed filesystem). **Not** a graph engine, not a retrieval library, not primarily an API.

[Source](https://github.com/letta-ai/letta) — retrieved 2026-09-11 · [docs.letta.com/llms.txt](https://docs.letta.com/llms.txt) — retrieved 2026-09-11

### Cognee — a graph-building memory engine (OSS + cloud + BYOC)

| Product | Description |
|---|---|
| **Cognee OSS** (`topoteretes/cognee`, Apache-2.0) | Python library: `pip install cognee`. Turns documents/code/app data into a knowledge graph + vector index + relational provenance store. Ships MCP server, CLI, HTTP API, Python API, Rust SDK, TypeScript SDK |
| **Cognee Cloud** | Managed memory service. Runs on `gpt-oss-120b` (OpenAI open-weight model) |
| **Cognee Enterprise** | Fixed-scope BYOC engagement: deployed in the customer's own VPC, with forward-deployed engineers (FDE), customer-specific evals, domain packs/ontology design |

**Category:** AI memory infrastructure — knowledge-graph memory engine. Positioned as an infrastructure/library layer under any agent (Claude Code, Codex, Cursor, LangGraph, OpenClaw), not a competing agent harness.

[Source](https://docs.cognee.ai/core-concepts/architecture) — retrieved 2026-09-11 · [cognee.ai/pricing](https://www.cognee.ai/pricing) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 3. Positioning & Messaging

### Letta

**Tagline (site headline):** "Machines that learn"

**Value proposition (verbatim):**
> "Letta is an AI research lab in San Francisco building machines that learn. We envision a future where humans coexist with digital people: experiential agents that remember everything, learn continuously, and improve themselves over time. Viva la machina."

**Repo descriptor (verbatim):** "Build stateful agents with memory that can learn and improve over time."

**Research framing (verbatim, docs):**
> "Letta agents learn by actively managing their own context — creating durable token-space representations of their identity, memory, and continuity — rather than by updating model weights."

**Product framing (verbatim):** "Our research ships as software: Letta Agent, a self-improving AI agent whose memory, identity, and capabilities evolve with experience."

**Brand voice:** Research-lab manifesto. Papers-first, rhetorical flourishes ("Viva la machina," "Context is selfhood"). Positions memory as *continuity of self*, not as retrieval quality. Notably does **not** run a "vs Mem0 / vs Zep" comparison-page play.

[Source](https://www.letta.com/) — retrieved 2026-09-11 · [docs.letta.com/llms.txt](https://docs.letta.com/llms.txt) — retrieved 2026-09-11

### Cognee

**Title / category line (verbatim):** "Cognee - Open-Source Agent Memory Platform"

**H1 (verbatim):** "Open Source Memory Platform for Agents"

**Value proposition (verbatim):** "Connect Slack, GitHub, Linear to Cognee and help agents recall what your company knows."

**Secondary positioning (verbatim):** "Cognee is the fastest way to start building reliable AI agent memory."

**Problem framing (verbatim, homepage):** "Agents get lost in your complex systems." → "Can't connect what you already know. Can't remember what you just did. Can't follow your rules." → "Cognee connects all your data into one brain." / "Cognee gives your agents memory." / "Cognee generates the ontologies your agents follow."

**Brand voice:** Developer + enterprise infrastructure. Benchmark-forward (BEAM SOTA), case-study-heavy (Bayer, Knowunity), SEO/GEO-comparison-heavy ("Cognee vs Zep", "Migrating from Mem0/Zep/Graphiti/Letta"), social-proof-driven (a wall of third-party X testimonials).

[Source](https://www.cognee.ai/) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 4. Target Audience

### Letta

| Segment | Details |
|---|---|
| Primary | Developers building **coding agents** and long-horizon assistants; secondarily individuals wanting a persistent personal agent |
| Named case studies | Bilt, 11x, Kognitos, Hunt Club |
| Use cases | Coding agents, personal assistants, "AI coworkers", agents embedded in third-party apps, agents reachable over Slack/Telegram/Discord/WhatsApp/Signal |
| Stack | Model-agnostic — Anthropic, OpenAI, Gemini, Vertex, Bedrock, Azure, xAI, Mistral, DeepSeek, Groq, Cerebras, Ollama, OpenRouter, and coding plans (ChatGPT Codex, Copilot, Kimi, zAI). Node.js 22.19+ for the CLI; Python SDK for the API |
| Surfaces | Desktop (macOS/Win/Linux), web, CLI, Messaging channels, cloud sandboxes |

**Not for:** Teams wanting a memory layer they drop under an existing framework — Letta wants to *be* the agent runtime, not a component under it.

[Source](https://www.letta.com/) · [docs.letta.com/pricing](https://docs.letta.com/pricing) — retrieved 2026-09-11

### Cognee

Explicitly segmented into three audiences on the homepage:

| Segment | Homepage framing |
|---|---|
| For teams | "Knowledge is scattered… Cognee connects all your data into one brain" |
| For agent builders | "Agent experience is discarded… Cognee gives your agents memory" |
| For enterprises | "Domain rules are guessed… Cognee generates the ontologies your agents follow" |

| Detail | Value |
|---|---|
| Personas | AI/agent engineers, data + platform teams, product engineers building vertical agents |
| Named users / case studies | Bayer, Knowunity (40K students, POC in 2 days), SlideSpeak, Dynamo, DeepMetis, Luccid, University of Wyoming (IEP project) |
| Client integrations | Claude Code, Codex, Cursor, OpenClaw, LangGraph, Hermes, MCP |
| Data connectors | Slack, GitHub, Linear, Notion, Google Drive, PostgreSQL, warehouses |
| Use cases documented | Coding-agent memory, company brain, support notes, procurement, HR résumé screening, building codes/regulations, vertical AI agents, session traces |
| Migration targets | Mem0, Zep, Graphiti, Letta (ships `LettaSource`, `ZepSource`, `GraphitiSource` importers) |

[Source](https://www.cognee.ai/) · [docs.cognee.ai/examples/migrate-memory-systems.md](https://docs.cognee.ai/examples/migrate-memory-systems.md) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 5. Business Model & Pricing

### Letta

**Revenue model:** Open-core + usage-based credits + per-seat. OSS harness is Apache-2.0 and self-hostable free; monetization is the hosted app/cloud, model credits ("Letta Auto"), and seats.

| Tier | Price | Key limits / features |
|---|---|---|
| **Free** | $0/mo | Limited agents (max 3 stateful agents), limited Letta Auto usage, BYOK, external coding plans |
| **Pro** | $20/mo | Letta Auto weekly + monthly quota, pay-as-you-go overage, up to 20 stateful agents |
| **API Plan** (Developer) | $20/mo | Unlimited agents, **$0.10 / active agent / month**, **$0.00015 / sec tool execution**, API keys, pay-as-you-go LLM usage |
| **Teams Pro** | $20/seat/month | Add teammates, share agents + access control, Letta Auto quota |
| **Enterprise** | Custom | Volume pricing, increased quotas, RBAC, SAML/OIDC SSO, dedicated support |

**Credit mechanics:** credits are a single unit for LLM inference and CPU; model requests consume credits at the underlying model's token pricing. BYOK routes usage through the provider instead of consuming Letta credits. Client-side tools (e.g. CLI bash) are free; server-side tools bill CPU at $0.00015/sec.

**Their own cost guidance (verbatim, docs):** "Casual coding users (a few hours per day): Typically ~$100/mo+ usage"; "Power users (long sessions, many parallel agents): Often approach or exceed $200+/mo in total usage."

**Open source:** Apache-2.0. Self-hosting supported: "Run Letta fully locally or on your own infrastructure — no Letta account required." MemFS **shared-memory repositories are a cloud feature**; self-hosted deployments must point agents at their own git remote.

✅ Verified against the live pricing page (`letta.com/pricing` redirects to `docs.letta.com/pricing`). [Source](https://docs.letta.com/pricing) — retrieved 2026-09-11

### Cognee

**Revenue model:** Open-core — Apache-2.0 self-host free → Cognee Cloud consumption pricing ($/token + $/workspace) → Enterprise BYOC engagements (fixed-scope, forward-deployed).

| Tier | Price | Key limits / features |
|---|---|---|
| **Free** | $0/mo | 1M tokens included, 1 workspace, unlimited users, unlimited API calls, agentic integrations (Claude Code, Codex, MCP) |
| **Standard** | **$1.00 / 1M tokens** + **$5 per additional workspace/mo** | Unlimited workspaces (at $5 each), data-source integrations (Slack, Notion, Linear, Google Drive), code indexing for repos, in-app support |
| **Enterprise** | Custom (BYOC engagement) | Everything in Standard **plus: bi-temporal memory & conflict resolution, provenance on every answer, personalization per user & agent**, dedicated Slack + support engineer, BYO cloud, support SLA. Engagement lengths: Startup (12 mo, discounted, pre-Series B only), Standard 6/12/24 mo |
| **OSS self-host** | Free forever | "Run the full memory engine locally or on your own stack — free, forever." Apache-2.0 |

⚠️ **Critical pricing/licensing asymmetry:** the pricing page lists **"Bi-temporal memory & conflict resolution"** and **"Provenance on every answer"** as *Enterprise-plan* features — even though the OSS docs describe a `valid_to` bi-temporal field (`close_node()`/`is_valid()`) and provenance tracking in the relational store. See §6 for the reconciliation.
⚠️ Enterprise tier has no public price — documented as a gap, not fabricated.

✅ Verified against the live pricing page. [Source](https://www.cognee.ai/pricing) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 6. Product & Features

### 6a. Letta — agent-managed context, git-backed

| Component | Detail |
|---|---|
| **MemFS** | The agent's long-term memory is **a git repository that belongs to the agent**, projected onto the machine as a real filesystem checkout. Requires Node.js 22.19+ CLI (`@letta-ai/letta-code`) |
| **Memory format** | Markdown files with YAML frontmatter, addressed by path. `system/persona.md`, `system/human.md`, `reference/*.md`, `skills/*/SKILL.md` |
| **In-context vs out-of-context** | Files under `system/` are loaded into the system prompt **every turn**. Files outside `system/` stay out of context — **the file tree itself is always in the system prompt**, so directory/file names act as signposts the agent follows |
| **Who edits memory** | **The agent itself.** "the agent edits it as it learns." Explicit teaching via `/remember`; bootstrap via `/init`; audit via `/doctor` |
| **Dreaming** | Background subagents review recent conversations, consolidate lessons, and update memory. Triggers: `step-count` or `compaction-event`; behavior `reminder` or `auto-launch` |
| **Shared memory** | Org-owned git repositories attached to multiple cloud agents; agents clone beside MemFS and commit/push |
| **Memory blocks (legacy)** | Pre-MemFS API: in-context blocks pinned to the system prompt, agent-editable via memory tools, attachable to multiple agents ("shared blocks"). Docs now call this the legacy pattern to migrate away from |
| **Versioning** | Every memory edit is committed to git → "version history, conflict resolution, and a clear boundary between saved memory and uncommitted changes." Memory subagents use git worktrees |

**Search & retrieval:**
- **MemFS has no semantic or vector index by default** (verbatim: "MemFS does not include a semantic or vector index by default. Agents find memory in its Markdown files with normal file-search and read tools.")
- Optional `memfs-search` mod adds keyword search (no extra deps) and optional semantic/hybrid modes (requires QMD indexed over `$MEMORY_DIR`)
- Conversation-history search is **separate** from MemFS: `letta messages search` supports full-text/vector/hybrid on Letta Cloud; local backends are full-text only

**Other surfaces:** skills (agent-authored, versioned in MemFS), mods (self-modifying the harness with local code), schedules, subagents, messaging channels, permissions/allowlists, secrets, cloud "computers"/sandboxes, teleportation, Letta Evals (open-source eval framework for stateful agents).

**Notable gaps (Letta):**
- ❌ No knowledge graph, no node/edge model of facts
- ❌ No confidence weights or belief scores on anything
- ❌ No typed logical relations (no implies/contradicts operators)
- ❌ No claim-level temporal validity windows
- ❌ No claim→source-turn provenance; the closest artifact is **git commit history of the memory file**
- ⚠️ Memory correctness depends entirely on the LLM deciding what to write; there is no verification layer

[Source](https://docs.letta.com/configuration/memory/index.md) · [MemFS](https://docs.letta.com/concepts/memfs/index.md) · [Shared memory](https://docs.letta.com/concepts/shared-memory/index.md) · [Agent SDK Memory](https://docs.letta.com/agent-sdk/memory/index.md) — all retrieved 2026-09-11

### 6b. Cognee — a knowledge graph with partial epistemics

**Three stores (verbatim roles):**
> "Relational store — Tracks your documents, their chunks, and provenance (i.e. where each piece of data came from and how it's linked to the source). Vector store — Holds embeddings for semantic similarity… Graph store — Captures entities and relationships in a knowledge graph."

Supports swapping backends: graph stores (Kuzu/Ladybug default, Neo4j, FalkorDB, Postgres, Neptune), vector stores (LanceDB default, PGVector, Qdrant…), relational (SQLite/Postgres).

**Core operations:** `remember()` / `recall()` / `improve()` / `forget()` (replaced the legacy `add`/`cognify`/`search`/`memify`).

**What the graph actually contains:**

| Element | Detail |
|---|---|
| Nodes | Extracted entities + types, `DocumentChunk`, `TextSummary`, `Document`, plus `Event`/`Interval` for time; newer `COGX*` models (`COGXEntity`, `COGXFact`, `COGXEpisode`) |
| Edges | `EdgeType` (relationship name + `edge_text`); edge collection `EdgeType_relationship_name` is always searched |
| Default model | `KnowledgeGraph` — default vector collections: `Entity_name`, `EntityType_name`, `TextSummary_text`, `DocumentChunk_text` |
| Entity extraction | LLM-based graph extraction (`extract_graph_from_data`) |
| Dedup | `Dedup()` annotation or `identity_fields` → deterministic UUID5; same `id` upserts in place. Ontology-matched individuals are collapsed onto one survivor with edges rewired |
| Ontology grounding | Optional RDF/OWL file via `ONTOLOGY_FILE_PATH` or a resolver. Matched nodes get `ontology_valid=True` + parent-class/object-property edges; unmatched entities kept verbatim (`annotate`, default) or dropped (`strict`). `ontology_uri` preserves the external IRI |

**Epistemic primitives (the part relevant to us):**

| Capability | Status | Detail |
|---|---|---|
| **Contradiction edge** | ✅ exists, ⚠️ **opt-in** | Contradiction detection writes a `contradicts` edge carrying both fact texts, the model's reason, and a **confidence score** (sample run: `confidence: 1.0`). "nothing is overwritten or deleted" |
| **Bi-temporal validity** | ✅ exists, ⚠️ node-level only | Every `DataPoint` gains `valid_to` (int ms-epoch, default `None` = still current). `close_node()` stamps it when a fact is superseded; `is_valid(node, at_ms)` checks currency. Closing is last-write-wins; **edges attached to the node are not stamped** |
| **Event/interval time** | ✅ | `Event`/`Interval` nodes with `time_from`/`time_to` — records when an event *occurred*, distinct from whether a fact still *holds* |
| **Edge-level bi-temporal** | ✅ (COGX + Graphiti import) | `COGXFact` relation edges carry bi-temporal `valid_at` / `invalid_at` windows; preserved when importing Zep/Graphiti exports |
| **Confidence / belief** | ⚠️ partial | Per-contradiction LLM-judged score; per-node **feedback weights** (rated 5/5 moved answer-used elements 0.5 → 0.55, `DEFAULT_FEEDBACK_INFLUENCE=0.2`). **No propagation/inference over the graph** |
| **Truth-subspace reranking** | ⚠️ **experimental, opt-in, off by default** | Builds up to k=8 anchor vectors from distilled session learnings, stores cosine "truth alignment" per chunk, nudges query-time ranking with `use_truth_weight=True` |
| **Provenance** | ✅ in OSS docs, ⚠️ Enterprise-marketed | Relational store tracks which documents/chunks each piece came from; an opt-in provenance ledger attributes nodes/relationships to their source root. Sessions record which graph elements each answer used. **But the pricing page lists "Provenance on every answer" as an Enterprise feature** |

**Backend caveat (verbatim from changelog):** `close_node()` persists only "on the default Ladybug (Kuzu) adapter"; "on backends without it, `close_node` logs a warning and returns `False`". Also explicit: "**No axioms are evaluated.** There is still no domain/range, cardinality, or disjointness reasoning, and no reasoner runs."

**Memory flow:** session cache (short-term, raw, no graph extraction) → `improve(session_ids=...)` distills lessons and pushes Q&A/traces into the permanent graph (self-improvement defaults on) → `recall(query, session_id=...)` checks session cache first, falls through to the graph, tags results with `_source`.

**Notable gaps (Cognee):**
- ❌ No belief propagation / no inference engine over the graph (no EP, no factor graph, no transitive implication closure)
- ❌ No general typed logical operators — `contradicts` is the only logical edge type found; there is no `implies`/support/attack primitive
- ⚠️ Contradiction detection is opt-in; confidence is an LLM judgement, not a calibrated, propagated number
- ⚠️ Bi-temporal validity is node-level (`valid_to`), not a general claim/edge-interval system; only the default backend supports it
- ⚠️ Graph accuracy depends on the extraction LLM and input structure (see §11)

### 6c. Architecture contrast — graph? reasoning state? (factual capability comparison)

| Dimension | Letta | Cognee | Tortoise (for reference) |
|---|---|---|---|
| Primary memory substrate | Git-backed Markdown filesystem (MemFS) + conversation DB | Knowledge graph + vector index + relational provenance store | Typed graph (points + operators) |
| Graph-structured? | ❌ No | ✅ Yes — extracted entities/relations | ✅ Yes — claims + typed operators |
| Who writes memory | **The LLM agent itself**, via file tools | An extraction LLM pipeline over ingested data | Explicit graph writes (agent/operator API) |
| Typed logical relations | ❌ None | ⚠️ `contradicts` edge only (opt-in) | ✅ IMPL (implies), NAND (contradicts) |
| Confidence / belief weights | ❌ None | ⚠️ LLM-judged per-contradiction score + feedback retrieval weights | ✅ Belief propagation over the graph |
| Temporal validity | ❌ None (git commit history only) | ⚠️ `valid_to` node stamps (supersede-not-delete), Event/Interval | ✅ Temporal validity / supersession |
| Provenance to source turn | ❌ (git commit history of the memory file) | ⚠️ relational store per document/chunk; claim-level ledger opt-in; "provenance on every answer" marketed Enterprise | ✅ Claim → source conversation turn |
| Retriever | File search / read tools; optional keyword+semantic mod | Vector seeds + graph traversal + hybrid (RRF-style) | Graph traversal with propagated confidence |
| Verdict | **Agent-managed context store over raw content — not a reasoning-state graph** | **Index/knowledge graph over raw content with partial epistemics — not a reasoning-state graph** (no propagation, no typed implication) | Reasoning-state graph |

*Last checked: 2026-09-11*

---

## 7. Go-to-Market & Acquisition

### Letta

| Channel | Activity |
|---|---|
| Open-source funnel | `letta-ai/letta` (24.7K stars, the MemGPT lineage) + `letta-ai/letta-code` (3.3K stars). Apache-2.0 |
| npm distribution | `npm i -g @letta-ai/letta-code` — **288K downloads/month** |
| Research / content | Own research blog: MemGPT → Sleep-time Compute → Context Repositories → Context Constitution → Memory Models. Press in WIRED, Fast Company, TechCrunch, Matthew Berman |
| Benchmark claim | "the **#1 model-agnostic open source agent on Terminal-Bench**" (their blog, via search snippet); **42.5%** is ⚠️ single-source (Ry Walker research). An **agent** benchmark, not a memory benchmark |
| Academic lineage | UC Berkeley Sky Computing Lab (birthplace of Spark and Ray); advisors Ion Stoica, Joey Gonzalez; MemGPT paper |
| Community | Discord (11.9K members) is the primary developer channel |
| Investor network | Felicis + angel list (Jeff Dean, Clem Delangue, etc.) |

**Sales motion:** Product-led OSS/desktop download → hosted cloud usage. Enterprise is a lightweight add-on (SSO/RBAC), not a heavy sales motion. Notably **absent**: a "vs competitor" comparison-page strategy.

⚠️ No data found on paid acquisition, DevRel headcount, or conference sponsorship.

[Source](https://www.letta.com/) · [npm registry API](https://api.npmjs.org/downloads/point/last-month/@letta-ai/letta-code) — retrieved 2026-09-11

### Cognee

| Channel | Activity |
|---|---|
| Open-source funnel | 30.6K GitHub stars, 151.7K PyPI downloads/month, `good-first-issue` labels cultivated for contribution |
| LLM-agent surface | First-party integrations for **Claude Code, Codex, Cursor, OpenClaw, LangGraph, Hermes** + MCP server — rides the coding-agent wave |
| Comparison + migration SEO | "Cognee vs Zep", "vs mem0", "vs Supermemory"; "Migrating from Mem0 / Zep / Graphiti / Letta" — direct poaching pages |
| GEO ("generative engine optimization") | Homepage ships a copy-paste prompt: *"I'm evaluating cognee… What does it do, how does it compare to Mem0, Zep and Letta, what are its strengths and weaknesses, and who is it best for?"* with Ask ChatGPT / Perplexity / Claude buttons |
| Social proof | Client testimonials + embedded third-party X posts (@svpino, @akshay_pachaar, @iruletheworldmo) |
| Case studies | Bayer, Knowunity, SlideSpeak, Dynamo, DeepMetis, Luccid, University of Wyoming |
| Enterprise FDE motion | BYOC engagements sold with forward-deployed engineers; "Every engagement starts with evals on your own data" |
| Accelerator | Part of **Berkeley Xcelerator** (on homepage) |
| Community | Discord (4.1K members), creator program, academy, biweekly newsletter |

**Sales motion:** OSS/self-serve → Cloud consumption → Enterprise BYOC (FDE-led). Startup-package discount for pre–Series B companies.

[Source](https://www.cognee.ai/) · [cognee.ai/pricing](https://www.cognee.ai/pricing) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 8. Traction & Scale

### Letta

| Signal | Value |
|---|---|
| GitHub stars | **24,699** (`letta-ai/letta`, Apache-2.0) + **3,274** (`letta-ai/letta-code`) |
| GitHub contributors | 155 (`letta-ai/letta`) |
| npm downloads | **288,317/month**, 40,685/week (`@letta-ai/letta-code`) |
| PyPI downloads | 66,483/month, 1,863/week (`letta`) |
| Discord | 11,911 members, 1,188 online |
| Funding | $10M Seed (Sep 2024), $70M post-money valuation |
| Team | 16 (Tracxn, Apr 2026) / 11–50 (LinkedIn) |
| Named customers / case studies | Bilt, 11x, Kognitos, Hunt Club |
| Published benchmarks | Terminal-Bench **42.5%** (agent benchmark); Letta Evals framework; `leaderboard.letta.com` for model performance |
| Press | TechCrunch, WIRED, Fast Company, Matthew Berman |
| ⚠️ Memory benchmarks | **No LongMemEval / LoCoMo score found** — Letta does not compete on memory-retrieval benchmarks |
| ⚠️ Revenue / ARR | No public data |
| ⚠️ Open issues | `letta-ai/letta` reports 0 open issues (issues appear disabled/redirected); `letta-code` has 353 |

[Source](https://api.github.com/repos/letta-ai/letta) · [npm](https://api.npmjs.org/downloads/point/last-month/@letta-ai/letta-code) · [pypistats](https://pypistats.org/api/packages/letta/recent) · [Discord API](https://discord.com/api/v9/invites/letta?with_counts=true) — all retrieved 2026-09-11

### Cognee

| Signal | Value |
|---|---|
| GitHub stars | **30,642** (`topoteretes/cognee`, Apache-2.0) — more stars than Letta's main repo |
| GitHub forks / contributors | 3,015 forks / 304 contributors |
| GitHub open issues | 221 open issues (520 including PRs) |
| PyPI downloads | **151,729/month**, 20,016/week, 3,127/day (`cognee`) |
| SDK runs | **5M+ SDK runs/month** (homepage) |
| Pipeline volume growth | ~2,000 runs → 1M+ runs (per seed announcement) |
| Discord | 4,078 members, 270 online |
| Funding | $7.5M Seed (Feb 2026), led by Pebblebed; ~$9M total |
| Published benchmarks | **BEAM 0.79 @ 100K** and **0.67 @ 10M** context (self-reported SOTA; prior reported SOTA 0.73 / 0.64). Their comparison page claims mem0 scores 0.48 @ 10M top-200 |
| Enterprise deployments | ⚠️ 70+ company deployments including Bayer — **single-source (Ry Walker)** |
| Case studies | Bayer, Knowunity, SlideSpeak, Dynamo, DeepMetis, Luccid, University of Wyoming |
| ⚠️ Revenue / ARR | No public data |
| ⚠️ LongMemEval / LoCoMo | No published LongMemEval or LoCoMo score found — BEAM is their published benchmark |

[Source](https://api.github.com/repos/topoteretes/cognee) · [pypistats](https://pypistats.org/api/packages/cognee/recent) · [BEAM benchmarking post](https://www.cognee.ai/benchmarking-cognee-on-beam) · [cognee.ai/research-and-evaluation-results](https://www.cognee.ai/research-and-evaluation-results) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 9. Online Presence & Content

### Letta

| Metric | Value |
|---|---|
| Main site | `letta.com` — research-lab homepage (papers, case studies, press) |
| Docs | `docs.letta.com` — ✅ live, high quality. Ships `llms.txt`, `/index.md` markdown endpoints for every page, and an "Ask Ezra" assistant |
| Web app | `chat.letta.com`; desktop apps for macOS/Windows/Linux |
| Model leaderboard | `leaderboard.letta.com` |
| Content strategy | **Research-first.** Named research artifacts: MemGPT (Oct 2023), Sleep-time Compute (Apr 2025), Continual Learning in Token Space (Dec 2025), Context Repositories (Feb 2026), Context Constitution (Apr 2026), Memory Models (Jun 2026) |
| Comparison content | Minimal — notably fewer "vs X" pages than Zep/Cognee have |
| ⚠️ Traffic / DA | No public data |

Docs quality is excellent and LLM-consumable — a deliberate developer-adoption play.

[Source](https://www.letta.com/) · [docs.letta.com/llms.txt](https://docs.letta.com/llms.txt) — retrieved 2026-09-11

### Cognee

| Metric | Value |
|---|---|
| Main site | `cognee.ai` — pricing, benchmarks, cost calculator, case studies, academy, newsroom, brand resources, events |
| Docs | `docs.cognee.ai` — Mintlify; ships `llms.txt`, `llms-full.txt` (3.9 MB), per-surface shards (`llms-core.md`, `llms-mcp.md`, `llms-api.md`) |
| Content strategy | **Benchmark + comparison + migration + ontology thought-leadership.** Pages: "AI Memory Benchmarks: The Complete Guide (2026)", "Cognee vs Zep/mem0/Supermemory", "Migrating from …", case studies, cost calculator |
| GEO play | Homepage prompt to ask ChatGPT/Perplexity/Claude about Cognee — deliberate LLM-answer optimization |
| ⚠️ Traffic / DA | No public data |

[Source](https://www.cognee.ai/) · [docs.cognee.ai/llms.txt](https://docs.cognee.ai/llms.txt) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 10. Community & Ecosystem

| Channel | Letta | Cognee |
|---|---|---|
| GitHub | 24,699 stars (letta) + 3,274 (letta-code), 155 contributors | 30,642 stars, 3,015 forks, 304 contributors |
| Discord | **11,911 members / 1,188 online** | **4,078 members / 270 online** |
| npm / PyPI | 288,317 npm downloads/mo | 151,729 PyPI downloads/mo |
| X/Twitter | Company handle not confirmed (footer link only) | Third-party advocate posts embedded on homepage (@svpino, @akshay_pachaar, @iruletheworldmo); no verified company follower count |
| LinkedIn | Company page, size band 11–50 | Not surfaced |
| Other | MemGPT academic citations; `letta-ai/mods` package registry | Creator program, academy, biweekly newsletter; Berkeley Xcelerator |

**Community mechanics:**
- **Letta** — Discord-centric, research-community flavored. Extensive contributor base on the legacy repo (155) relative to team size (16). Ecosystem extension point is *mods* (agents self-modifying the harness) plus an npm mod registry.
- **Cognee** — contributor-growth flavored (304 contributors, `good-first-issue` / `good-first-pr` labels), with a structured creator program and academy. Ecosystem extension point is *integrations* (frameworks, connectors, MCP clients) plus migration importers for rival memory systems.

⚠️ X/Twitter and LinkedIn follower counts could not be verified for either company without an authenticated session — documented as gaps.

[Source](https://discord.com/api/v9/invites/letta?with_counts=true) · [Discord API (cognee)](https://discord.com/api/v9/invites/NQPKmU5CCg?with_counts=true) · GitHub API — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 11. Customer Sentiment

### Letta

**Sources checked:** GitHub issues (letta-ai/letta), Letta blog, third-party review pages (aitoolsatlas, vectorize.io comparison), Reddit/HN via search.

**What users praise:**
- Research pedigree — Berkeley Sky Computing Lab, MemGPT authors, advisors Ion Stoica / Joey Gonzalez
- The git-versioned MemFS / Context Repositories model — memory changes are reviewable, diffable, and mergeable
- Genuinely model-agnostic (every frontier provider + local models + coding plans)
- Apache-2.0 with a full self-host path, and an active Discord (11.9K)
- No vendor lock on memory format — memory is plain Markdown files

**What users complain about:**
- **Complexity / steep learning curve.** GitHub issue #490: MemGPT is "one of the hardest pieces of AI software to get to grips with." A third-party comparison: Letta "has a steeper learning curve, with concepts like core/recall/archival memory and Python-only setup taking more time to internalize"
- **Integration friction.** Issue #689: MemGPT "did not update the expected memory layer"
- **Concept churn.** MemGPT → Letta server → Letta Code pivot; heartbeats deprecated; memory blocks now "legacy." Docs explicitly tell users to migrate off the shared-blocks pattern
- **Self-hosting/ops effort** and **unpredictable usage cost** — their own docs warn casual coding users to expect ~$100/mo+ and power users $200+/mo
- No semantic index by default — retrieval quality rests on the agent's own file-reading behavior

[Source](https://github.com/letta-ai/letta/issues/490) · [Issue #689](https://github.com/letta-ai/letta/issues/689) · [vectorize.io comparison](https://vectorize.io/articles/mem0-vs-letta) · [aitoolsatlas review](https://aitoolsatlas.ai/tools/memgpt/review) — retrieved 2026-09-11

### Cognee

**Sources checked:** Reddit (r/LLMDevs, r/AI_Agents), GitHub issues, third-party review pages, cognee.ai case studies/testimonials.

**What users praise:**
- Time-to-first-memory — "give your agents memory in 60 seconds"; Homepage/PH-style quote: "Been using cognee for over 7 months now after migrating from Graphiti. Great product."
- Knowledge-graph framing resonates with the "RAG isn't enough" audience: "Knowledge graphs for representing information are unbeatable. I used cognee." (@svpino)
- Integration breadth into coding agents (Claude Code, Codex, Cursor) and MCP
- Forward-deployed support: "Cognee **and the FDE team** have been terrific for us. We launched the first memory system within 30 days" (University of Wyoming)
- Benchmark transparency — BEAM 0.79/0.67 published with a methodology page
- Fast POCs: Knowunity — "we managed to get a POC done in 2 days on 40,000 students"

**What users complain about:**
- **Ingestion quality / duplication.** Reddit: "There was a significant amount of nearly identical memories being stored… the same information kept getting pushed to Cognee repeatedly." Another thread warns Cognee "may assume input is already well-structured"
- **Permissions friction.** Reddit: "The graph endpoint still enforces per-user permissions and throws 403s even though dataset listing works fine"
- **Setup & ops.** GitHub issues: install errors ("psycopg2 only satisfied when PostgreSQL is installed"), "The behavior of path handling in the `.env` file is inconsistent", "Insights does not work with custom datapoints insertions". Review: "self-host setup is a real ops project"; Neo4j "adds infrastructure complexity"
- **Extraction quality is LLM-dependent** — "graph extraction quality depends on the LLM you run the pipeline with"
- **Overkill for simple use cases** — "overkill for simple FAQ or single-document retrieval"

**Overall sentiment (both):** **Positive with material DX/ops friction.** Letta = strong research brand + community + git-memory, weak on ease-of-use and on explicit memory semantics. Cognee = strong star/download growth + benchmark/marketing machine + graph-native memory, weak on ingestion robustness and self-host operations.

[Source](https://www.reddit.com/r/LLMDevs/comments/1uzyyja/cognee_10_oss_selfimproving_memory_for_agents_scoring_79_on_beam/) · [Reddit r/AI_Agents](https://www.reddit.com/r/AI_Agents/comments/1s42kmu/some_thoughts_on_working_with_memory_systems/) · [github.com/topoteretes/cognee/issues](https://github.com/topoteretes/cognee/issues) · [aitoolsatlas Cognee review](https://aitoolsatlas.ai/tools/cognee/review) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## Notes & Sources

### Primary sources (fetched live)

**Letta**
- [letta.com](https://www.letta.com/) — homepage: positioning, research chronology, case studies (Bilt, 11x, Kognitos, Hunt Club), investors, press. Retrieved 2026-09-11
- [docs.letta.com/pricing](https://docs.letta.com/pricing) — live pricing (Free/Pro/API/Teams Pro/Enterprise) + FAQ. `letta.com/pricing` redirects here. Retrieved 2026-09-11
- [docs.letta.com/llms.txt](https://docs.letta.com/llms.txt) — canonical product framing, surfaces, model providers, docs index. Retrieved 2026-09-11
- [docs.letta.com/configuration/memory/index.md](https://docs.letta.com/configuration/memory/index.md) — MemFS, `/remember`, `/init`, `/doctor`, dreaming config. Retrieved 2026-09-11
- [docs.letta.com/concepts/memfs/index.md](https://docs.letta.com/concepts/memfs/index.md) — MemFS structure, no-default-vector-index statement, versioning, skills. Retrieved 2026-09-11
- [docs.letta.com/concepts/shared-memory/index.md](https://docs.letta.com/concepts/shared-memory/index.md) — shared repositories; memory blocks described as legacy. Retrieved 2026-09-11
- [docs.letta.com/agent-sdk/memory/index.md](https://docs.letta.com/agent-sdk/memory/index.md) — SDK memory API, dreaming options. Retrieved 2026-09-11
- [letta.com/blog/context-repositories](https://www.letta.com/blog/context-repositories) — Feb 12, 2026 git-based memory rebuild. Retrieved 2026-09-11
- [github.com/letta-ai/letta](https://github.com/letta-ai/letta) — README: "current source code lives in letta-ai/letta-code". Retrieved 2026-09-11
- GitHub API: `letta-ai/letta` (24,699 stars, 2,616 forks, 155 contributors, Apache-2.0, created 2023-10-11, last push 2026-09-10); `letta-ai/letta-code` (3,274 stars, created 2025-10-25, 353 open issues). Retrieved 2026-09-11
- npm: `@letta-ai/letta-code` 288,317 downloads/month. Retrieved 2026-09-11
- PyPI: `letta` 66,483 downloads/month, version 0.16.8. Retrieved 2026-09-11
- Discord API invite `letta`: 11,911 members / 1,188 online. Retrieved 2026-09-11
- [TechCrunch](https://techcrunch.com/2024/09/23/letta-one-of-uc-berkeleys-most-anticipated-ai-startups-has-just-come-out-of-stealth/) — $10M seed, $70M post-money, founders. Retrieved 2026-09-11
- [PR Newswire seed announcement](https://www.prnewswire.com/news-releases/berkeley-ai-research-lab-spinout-letta-raises-10m-seed-financing-led-by-felicis-to-build-ai-with-memory-302257004.html) — Felicis lead, Sunflower Capital, Essence VC. Retrieved 2026-09-11
- [LinkedIn company page](https://www.linkedin.com/company/letta-ai) — 11–50 employees, founded 2024. Retrieved 2026-09-11
- [Tracxn profile](https://tracxn.com/d/companies/letta/__U0QCJ7iCo3pX97e44fpKmKTCiUKKF6Ki8rWRAUaHxHY) — 16 employees (2026-04-30), $10M raised. Retrieved 2026-09-11

**Cognee**
- [cognee.ai](https://www.cognee.ai/) — homepage: positioning, three-segment framing, 5M+ SDK runs/month, 30.6k stars, Bayer/Knowunity case studies, testimonials, integration list, "Ask an AI" GEO block. Retrieved 2026-09-11
- [cognee.ai/pricing](https://www.cognee.ai/pricing) — live pricing (Free/Standard/Enterprise/BYOC engagement tiers) + the Enterprise-only feature list (bi-temporal & conflict resolution, provenance on every answer). Retrieved 2026-09-11
- [docs.cognee.ai/core-concepts/architecture](https://docs.cognee.ai/core-concepts/architecture) — three-store architecture, provenance role. Retrieved 2026-09-11
- [docs.cognee.ai/core-concepts/data-flows](https://docs.cognee.ai/core-concepts/data-flows) — session learning / self-improvement / ingestion pipelines. Retrieved 2026-09-11
- [docs.cognee.ai/examples/contradiction-handling](https://docs.cognee.ai/examples/contradiction-handling) — `contradicts` edge with reason + confidence; feedback weights 0.5→0.55; nothing deleted. Retrieved 2026-09-11
- [docs.cognee.ai/core-concepts/further-concepts/ontologies](https://docs.cognee.ai/core-concepts/further-concepts/ontologies) — RDF/OWL grounding, annotate vs strict, `ontology_valid`, "no reasoner runs". Retrieved 2026-09-11
- [docs.cognee.ai/guides/truth-subspace-reranking](https://docs.cognee.ai/guides/truth-subspace-reranking) — experimental, opt-in, off by default; k=8 anchors. Retrieved 2026-09-11
- `docs.cognee.ai/llms-full.txt` (3.9 MB) — changelog entries for fact validity: `valid_to` bi-temporal field, `close_node()`, `is_valid()`, default Kuzu/Ladybug-only persistence (SDK-200, PR #4105); `COGXFact` bi-temporal `valid_at`/`invalid_at`; `identity_fields`/`Dedup()` dedup; transparent containers + chunk attachment (SDK-163, PR #4682). Retrieved 2026-09-11
- [github.com/topoteretes/cognee README](https://github.com/topoteretes/cognee) — Discord invite, docs links. Retrieved 2026-09-11
- GitHub API: `topoteretes/cognee` — 30,642 stars, 3,015 forks, 304 contributors, Apache-2.0, created 2023-08-16, 221 open issues (520 incl. PRs). Retrieved 2026-09-11
- PyPI: `cognee` 151,729 downloads/month, version 1.5.4. Retrieved 2026-09-11
- Discord API invite `NQPKmU5CCg`: 4,078 members / 270 online. Retrieved 2026-09-11
- [Cognee seed announcement](https://www.cognee.ai/cognee-raises-seven-million-five-hundred-thousand-dollars-seed) — $7.5M led by Pebblebed, with 42CAP and Vermilion Ventures; pipeline volume 2,000 → 1M+ runs. Retrieved 2026-09-11
- [Trending Topics (Berlin)](https://www.trendingtopics.eu/cognee-berlin-startup-raises-7-5-million-builds-long-term-memory-for-ai-agents/) — Berlin HQ, founded 2024. Retrieved 2026-09-11
- [cognee.ai/about-us](https://www.cognee.ai/about-us) — team page (Vasilije Markovic, CEO & founder). Retrieved 2026-09-11
- [Caplight](https://www.caplight.com/company/cognee) / [Seedtable](https://www.seedtable.com/startups/Cognee-EW98RKN) / [TheCompanyCheck](https://www.thecompanycheck.com/company/b/cognee/zo7d4gvxj80d47h59) — total funding $8.9M–$9.09M across 2 rounds. Retrieved 2026-09-11
- [BEAM benchmarking post](https://www.cognee.ai/benchmarking-cognee-on-beam) and [research & evaluation results](https://www.cognee.ai/research-and-evaluation-results) — 0.79 @ 100K, 0.67 @ 10M. Retrieved 2026-09-11

### Gaps documented (not fabricated)

- ⚠️ **Letta memory benchmarks:** no LongMemEval or LoCoMo score found. Its published benchmark (Terminal-Bench 42.5%) measures agent task performance, not memory retrieval quality. Do not compare it to Zep/Hindsight memory scores.
- ⚠️ **Cognee LongMemEval/LoCoMo:** no published score found; BEAM is their benchmark. BEAM scores are self-reported.
- ⚠️ **Cognee team size:** not disclosed anywhere public (site, press, aggregators). Do not state a number.
- ⚠️ **Cognee total funding:** aggregators disagree ($8.9M / $9M / $9.09M). Only the $7.5M seed is company-confirmed.
- ⚠️ **Letta team size:** conflicting (LinkedIn band 11–50 vs Tracxn 16). Reported as a range.
- ⚠️ **Revenue / ARR:** no public data for either company.
- ⚠️ **Traffic / DA / DR:** not in public indices for either; no paid SimilarWeb/Ahrefs access.
- ⚠️ **X/Twitter + LinkedIn follower counts:** not verifiable without an authenticated session.
- ⚠️ **"70+ company deployments including Bayer" (Cognee):** single-source (Ry Walker research page). Bayer's case study is confirmed on cognee.ai; the count of 70+ is not.
- ⚠️ **Letta Discord member count** was retrieved via the Discord invite API (public, unauthenticated) — accurate for the invite, may drift.
- ⚠️ **Cognee Enterprise pricing:** custom only; no public numbers. The BYOC tier table describes scope, not price.
- ⚠️ **Provenance contradiction (Cognee):** OSS docs describe provenance tracking (relational store) and an opt-in provenance ledger, while the pricing page markets "Provenance on every answer" as an Enterprise feature. Both are recorded; the exact OSS/Enterprise boundary is unresolved.
- ⚠️ **`letta-ai/letta` reports 0 open issues** — issues appear disabled or redirected on that repo; use `letta-code` (353 open) for issue-volume signal.
- ⚠️ **pypistats.org rate-limited** on several attempts; figures above are from successful calls.

### Suggested follow-up
- Read the MemGPT paper and Letta's "Memory Models" / "Context Constitution" research posts for the theoretical claim that token-space context management substitutes for a graph.
- Run Cognee's `contradiction_feedback_demo.py` and `fact_validity.py` locally to verify the `contradicts` edge, confidence score, and `valid_to` behavior on a non-default backend (the documented Kuzu/Ladybug limitation).
- Check whether Cognee's `valid_to` and `contradicts` are available in Cognee Cloud outside the Enterprise tier (pricing page implies not).

*Last updated: 2026-09-11*
