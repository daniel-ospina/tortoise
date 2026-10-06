# Kumiho

> Graph-native agent memory with a formal AGM belief-revision correspondence — the closest published analogue to Tortoise's graph-as-belief-state thesis. Both a preprint (arXiv:2603.17244) and a shipped commercial product (kumiho.io).

**⚠️ Evidence quality flag:** the central claims come from a **single-author preprint by the vendor** (`© 2026 Kumiho Inc.`, CC BY-NC-ND 4.0, not peer-reviewed). Every benchmark number below is an **UNVERIFIED VENDOR CLAIM** unless explicitly marked otherwise.

---

## 1. Overview

| Field | Value |
|---|---|
| Legal entity | Kumiho Inc. (`© 2026 Kumiho Inc.` on kumiho.io footer and on the paper) |
| Founded | Not published. Earliest public artifact: `kumihoclouds/kumiho-dart` repo created 2026-01-02; `KumihoIO` GitHub org created 2026-03-03; paper submitted 2026-03-18. Company likely predates the public footprint — the About page says the graph originated in **VFX/animation production pipelines** ("asset management systems in visual effects and game development have implemented these exact primitives for decades") |
| HQ | GitHub org lists location as "United States of America"; **no street address or city published anywhere found**. Site is bilingual EN/KO — a Korean connection is plausible but unconfirmed |
| Funding raised | ⚠️ **No public funding data found.** No Crunchbase/Tracxn/press coverage located. Not verifiable |
| Team size | "a small team of creators and pipeline engineers" (their words). **No named founders, no team page, no headcount data.** Sole paper author: **Young Bin Park**; contact `support@kumiho.io` |
| IP | "Patent-pending architecture filed with the USPTO" — claimed on the About page, no patent number published |
| Markets | (a) AI agent / developer tooling (agent memory), (b) creative-industry asset management (VFX, animation, games) — the origin market |
| Public identity | Two GitHub orgs: [`KumihoIO`](https://github.com/KumihoIO) (18 repos, agent-facing, created 2026-03-03) and `kumihoclouds` (release/plugin repos, back to 2026-01-02) |

**⚠️ Gaps:** no registered company filing located, no founders named, no funding, no HQ, no team size. This is a very early-stage, low-disclosure company with an unusually credible technical artifact.

*Last checked: 2026-09-11*

---

## 2. Product Type

**Both — a preprint AND a shipped product.** This is not paper-only.

| Surface | What it is | Status |
|---|---|---|
| **Kumiho Memory Engine** | The shared, versioned memory graph (Rust gRPC server + Neo4j; Redis working memory). **Proprietary — cloud service only.** Paper footnote 1: "The core graph server is provided as a cloud service at https://kumiho.io. The Python SDK, MCP memory plugin, and benchmark suite are open-source" | Shipped (Cloud) |
| **Community Edition (CE)** | Free, **single-user, loopback-only (binds to 127.0.0.1)**, self-hosted, no account/token/cloud connection. Requires local **Neo4j 5.x** (Redis 7.x optional). One-line installer; replaces the retired cloud Free tier | Shipped (v1.8.0, 2026-09-08) |
| **Kumiho Cloud** | Hosted plans (Creator/Studio/Studio Pro/Enterprise) with "AI Cognitive Memory included" | Shipped, paid |
| **Kumiho Desktop** | Cross-platform desktop app (Flutter/Dart per paper, Rust per repo) — installs CE, connects to Cloud, installs plugins, auto-updates. Windows (.exe 47.5 MiB, **not code-signed**), macOS (signed + notarized, Apple Silicon + Intel), Linux (AppImage/.deb/.rpm) | Shipped (v0.5.0) |
| **MCP server + SDKs** | 51 MCP tools; Python, C++, Dart SDKs; `pip install kumiho-memory`; Claude Code plugin marketplace install | Shipped, open-source |
| **Revka** | Separate product (`revka.ai`, `KumihoIO/Revka`): "Memory-native AI agent runtime" — Rust gateway + React dashboard + Python Operator; spawns Claude Code / Codex / Cursor / Antigravity / OpenCode as governed agents with a Merkle hash-chain audit log | Shipped (45★) |
| **9Miho / Kumiho Browser** | 9Miho = image → asset → reference → video skills with provenance; Kumiho Browser = revision/lineage browser. Both announced as "on the way" | In progress |
| **Plugins** | ComfyUI custom nodes, n8n nodes (asset/memory change triggers) | Shipped |

**Architectural character:** a *dual-store graph engine with an MCP/SDK front*. The graph is the system of record; embeddings and the LLM are derived/auxiliary layers. The commercial strategy is two products on one graph: assets (creator market, revenue) + memory (agent market, growth).

**Relationship to the paper:** the paper is the **specification**; the product is the **reference implementation** whose claimed compliance artifacts (49/49 AGM suite, benchmark harness) are published in the repo. Not all paper components are GA — the paper itself says multi-agent pipeline validation for the asset-management half is future work.

*Last checked: 2026-09-11*

---

## 3. Positioning & Messaging

**Primary tagline:** **"The memory layer AI agents can trust."**

**Secondary taglines (verbatim):**
- "One graph. Every surface."
- "Inside the memory engine — A cognitive memory core, not an embedding cache."
- "Built on a versioned provenance graph… Agent memory inherits that rigor; it isn't a vector store with a marketing layer."

**Value proposition (their words, homepage):**
> "Kumiho gives every agent a shared, versioned memory graph — so facts can be traced, corrected, and trusted across tools."
> "Formal belief revision · typed provenance · local-first"

**Problem framing (verbatim, homepage):**
> "Agents forget. Vector stores can't say why they remember. Flat similarity search breaks on exactly the questions long-running agents get asked. One root cause: memory without structure."

with three named failure modes: *multi-hop* ("Similarity search retrieves fragments; nothing connects them"), *temporal* ("Embeddings have no timeline. Without valid-time, 'latest' and 'true back then' blur into one"), *belief update* ("Append-only memory accumulates contradictions. A real update revises what depended on the old belief").

**Brand voice:** **proof-first, anti-hype, adversarial-to-itself.** Two explicit commitments on the About page:
> "**Receipts over adjectives.** Every claim carries its number, citation, or link. If a proof artifact is not public yet, the claim waits."
> "**Receipts, not adjectives.** Every number below links to a public artifact you can re-run. Where a peak isn't reproducible on today's models, we say so — and don't quote it."

The About page headline is a positioning pivot worth noting: **"We built provenance for studios. Agents turned out to need it more."**

**Competitive posture:** differentiation is stated **on the formal axis, not the feature axis** — "Immutable revision history", "Formal belief revision", "URI-based addressing", and "Typed edge ontology (≥6)" are marked present for Kumiho and absent for Graphiti / Mem0g / A-MEM / Letta / MAGMA / Hindsight / MemOS in the paper's Table 9. Named comparison targets in prose: Graphiti/Zep, Mem0/Mem0g, MemGPT/Letta, Hindsight, MAGMA, MemOS, A-MEM. **Tortoise is not mentioned anywhere in the paper or on the site.**

[Source](https://kumiho.io/) and [Source](https://kumiho.io/en/about) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 4. Target Audience

**Two distinct segments served by one graph:**

| Segment | Who | Evidence |
|---|---|---|
| **AI agent builders** | Developers running MCP-based agents — Claude Code, Codex, Cursor, OpenCode, Antigravity CLI; "your own stack" | Explicit CLI list on homepage and revka.ai; Claude Code plugin marketplace install command; `pip install kumiho-memory` for other MCP clients |
| **Creative / studio pipelines** | VFX, animation, game studios needing revision + dependency + provenance tracking for assets | About page: "We spent years inside VFX, animation, and AI workflows"; pricing "built for asset operations — now with Cognitive Memory"; Kumiho Browser, ComfyUI plugin |

**Explicit buyer constraints they advertise:**
- **Local-first / privacy-first:** "Not with Community Edition — the server binds to loopback only and stores everything in your local Neo4j. Full conversations can stay local as markdown artifacts; if you opt into cloud, short structured summaries are what sync."
- **Model-agnostic:** "Kumiho does not provide or resell LLM inference. Connect your own OpenAI, Anthropic, local model, or AI agent runtime." (BYO-LLM is stated as a *feature*, and pricing is infrastructure/indexing only.)
- **Governance/audit buyers:** "Every agent belief has a URI, a revision history, provenance edges to source evidence, and an immutable audit trail" → positions for teams that need "the same accountability standard applied to human workers."

**Use cases advertised:** decision memory ("Why is the code like this? Ask it. Decisions anchor to commits and files, with evidence and rejected alternatives attached"), multilingual recall ("Native Korean morphological search with typo-tolerant matching"), history backfill ("Backfill Claude, ChatGPT, and Codex into one live graph").

**Not for (inferred):** hobbyists wanting a vector store; anyone unwilling to run Neo4j (CE **requires** a local Neo4j 5.x — "Do I need Neo4j? Yes").

*Last checked: 2026-09-11*

---

## 5. Business Model & Pricing

**Revenue model:** seat/tier subscription on hosted graph infrastructure + node-count indexing, **BYO-LLM** (they explicitly do not resell inference) + Enterprise contracts. Free single-user self-hosted CE as the developer/OSS funnel (it explicitly "replaces the retired cloud Free tier").

### Cloud plans (kumiho.io/en/pricing, retrieved 2026-09-11)

| Tier | Price | Nodes | Working-mem retention | Memory ingest/day | AI Memory Ops/mo | Vector-indexed items/mo | Events/day |
|---|---|---|---|---|---|---|---|
| **Creator** | $40/mo | 30,000 | 24h | 500 | 25,000 | 10,000 | 2,000 |
| **Studio** (most popular) | $99/mo | 500,000 | 24h | "Included" | 150,000 | 100,000 | 5,000 |
| **Studio Pro** | $170/mo | 2,000,000 | 72h | "Included (higher)" | 400,000 | 250,000 | 10,000 |
| **Enterprise** | Contact sales | Unlimited | Custom | Custom | Custom | Custom | 50,000 |
| **Community Edition** | Free | No data caps (local) | — | — | — | — | — |

- Monthly/yearly toggle exists; **annual prices were not captured on fetch** (page defaults to monthly) — ⚠️ gap.
- **API rate limit 100 req/sec on every tier** including Creator.
- **Trial:** "30 days of Studio, free… activates the moment you save your first memory — 500,000 nodes, cross-session recall, audit visibility. No credit card."
- **Referral:** "+30 days per signup, stackable up to 90"; beyond cap converts to account credit.
- **"AI Memory Ops"** is their metering unit: "store + retrieve + consolidate. These can map to event stream throughput."
- **Over-limit behavior:** "Add usage packs, upgrade tiers, or apply throttles and retention policies."
- **Churn handling:** "Inactive accounts move to cold storage after 90 days — we never silently delete your memories. Log back in at any time and the graph re-indexes within minutes."
- **Tier limits detail page** exists ("See detailed limits") for retention, cadence, graph scale.

**Community Edition limits (free):** single-user, loopback-only, no data caps, requires local Neo4j 5.x, "Not for hosted, team, or production-backend use."

**Who pays:** small studios and teams for the asset/platform side; agent-memory developers for graph scale (nodes, memory ops) and cross-session/team features. LLM cost is entirely off-platform.

**⚠️ Unverified:** no published revenue, ARR, customer count, or paid-conversion data anywhere.

[Source](https://kumiho.io/en/pricing) — retrieved 2026-09-11

*Last checked: 2026-09-11*

---

## 6. Product & Features

**Platform:** MCP server + Python/C++/Dart SDKs + cloud API + self-hosted server + desktop app + web dashboard + plugins (ComfyUI, n8n) + a separate runtime (Revka).

### 6a. The graph model (from the paper — VERBATIM structural definitions)

**Dual-store:** "a dual-store model (Redis working memory, Neo4j long-term graph)." Working memory: Redis via direct library SDK (not HTTP), configurable TTL default 1 hour, default 50-message buffer, session-scoped keys `cogmem:{proj}:sessions:{sid}:*`, **2–5 ms measured** vs 150–300 ms via HTTP gateway.

**Item–Revision model:** "Each memory unit is represented as an **Item** node with one or more **Revision** nodes forming an immutable version chain." Only **tags** are mutable:
> "**Principle 5 (Immutable Revisions, Mutable Pointers).** Memory states are never overwritten; they are versioned."

Worked example from §6.4.1:
```
Item: "api-design.decision"
  Rev1 (Jan 15): "Use REST for public API"
  Rev2 (Jan 22): "Use REST + WebSocket"   edge: SUPERSEDES -> Rev1
  Rev3 (Feb  1): "Use gRPC internally"    edge: SUPERSEDES -> Rev2
                                          edge: DERIVED_FROM -> "benchmarks.fact?r=1"
  Tag "current" -> Rev3
  Tag "initial" -> Rev1
```

**URI addressing:** `kref://project/space/ item.kind?r=N&a=artifact` — "Addressability… Temporal navigation… Type safety… Traversal entry points." Claimed unique: "Among agent memory systems, we found no prior use of structured, hierarchical URIs with these properties."

**Six typed, directed edge types** (§6.5, "Reasoning as First-Class Structure"):

| Edge | Semantics (their words) |
|---|---|
| `Depends_On` | "Validity dependency. If the target is invalidated, the source may be unreliable." |
| `Derived_From` | "Evidential provenance. The source was produced using the target as input." |
| `Supersedes` | "Belief revision. The source replaces the target as the current belief." |
| `Referenced` | "Associative mention. The source refers to the target without dependency." |
| `Contains` | "Bundle membership." |
| `Created_From` | "Generative lineage." |

> **⚠️ Note for our analysis: there is NO contradiction / negation / NAND edge type.** Conflict is represented exclusively as `Supersedes` + tag-pointer movement. Probe/question edges do not exist as a primitive. (Dashboard legend adds `Belongs_To` as a 7th edge in the UI.)

**Six memory types:** working (Redis TTL), episodic (conversation revisions), semantic (consolidated facts), procedural (tool execution), associative (edges + bundles), meta (tags + audit trail).

**Traversal operations:** `TraverseEdges(k,d,n)`, `ShortestPath(k_s,k_t)`, `AnalyzeImpact(k,d)` — "when an agent discovers that assumption A is invalid, AnalyzeImpact identifies all downstream conclusions that may need re-evaluation."

**Design principles (Table 10):** 1 Structural Reuse · 2 Universal Addressability · 3 Immutable Revisions / Mutable Pointers · 4 Explicit Over Inferred Relationships · 5 Non-Blocking Enhancement · 6 Conservative Memory Management · 7 Metadata Over Content. (Plus 10 inline principles incl. "Validate at Boundaries, Trust Internally", "Match Storage Latency to Access Pattern", "Why Graph-Native Edges Matter", "Explicit Over Inferred Relationships", "Non-Blocking Enhancement", "Conservative Memory Management".)

### 6b. Belief revision: **BOOLEAN, not probabilistic** — the decisive finding

The formal system is **propositional logic over ground triples** (`At_G`), explicitly chosen as *weak*:

> "The formal results hold for a **deliberately weak propositional logic over ground triples**. This logic cannot express subsumption hierarchies, role composition, disjointness axioms, or cardinality constraints… Any strengthening of L_G toward richer logics would re-encounter Flouris-type impossibility results."

- Proved: **AGM K\*2 (Success), K\*3 (Inclusion), K\*4 (Vacuity), K\*5 (Consistency), K\*6 (Extensionality)** + **Hansson's Relevance and Core-Retainment**, at the **belief-base** level (Hansson 1999), not belief-set.
- **Recovery is deliberately rejected**: "The graph-native architecture deliberately violates this postulate; we argue that this violation is a principled design decision" — because revisions are never erased, only archived, and re-expansion is "a fresh incorporation of φ, not a rollback to a prior state."
- **K\*7 / K\*8 are OPEN**: "The supplementary postulates (K\*7, K\*8) remain open—establishing them requires constructing an entrenchment ordering." They explicitly decline to construct it.
- The revision operator is **deterministic and boolean**: revision = create immutable Revision node + `Supersedes` edge + move tag pointer. Contraction = tag removal / `deprecated` flag. Expansion = add revision. No weights are combined, no credence is updated, **no probability distribution exists anywhere in the belief state**.
- **How conflicts are represented:** a *new revision* supersedes an *old revision*; the old one leaves the retrieval surface only if its tag is moved away. §8.6: "When both a current and a superseded belief appear in retrieval results… **the system does not automatically resolve the conflict**; instead, it provides the agent with temporal metadata (creation timestamps, revision numbers) that the agent's reasoning layer uses to apply recency preference."
- **The only numeric score near "confidence"** is the Dream State LLM assessment: `Relevance: How useful is this memory for future agent interactions? (0.0–1.0)` — an **LLM heuristic relevance score for consolidation decisions**, which the paper itself flags as a *candidate* entrenchment input, "not obviously canonical", and "depend[ent] on LLM assessment quality, introducing a non-formal dependency." **It is not part of the belief state and does not propagate.**
- **One truth layer / no uncertainty:** "the formal apparatus" uses classical negation while "operationally the system realizes this through the closed-world assumption: the atom's absence from B(τ) is treated as if ¬φ holds at the retrieval surface."

### 6c. Retrieval surface — derived from the belief state (this is the key overlap with Tortoise)

§8.6 "Retrieval Semantics Under Belief Revision" closes the loop explicitly:
> "belief revision operations (revision, contraction, expansion) modify the tag assignment τ and deprecation status, which **deterministically define the retrieval surface**, which in turn bounds what the agent can encounter through any retrieval modality."

- **Belief base:** `B(τ) = ⋃_{t∈dom(τ)} φ(τ(t))` — the content of all currently tag-referenced revisions.
- **Enforcement is at the Cypher layer:** "only revisions belonging to non-deprecated items are candidates for scoring. This filter is enforced at the Cypher query level (a `WHERE NOT item.deprecated` clause), not at the application level, making it architecturally guaranteed rather than convention-dependent." Neither fulltext nor vector retrieval can surface deprecated content without `include_deprecated=true` (an operator-only flag).
- **Time-indexed belief states:** the tag history function `τ_T` answers "what was believed under tag t at time T?" — "not by scanning revision timestamps, but by resolving the actual tag-to-revision binding that was active at T."
- **Hybrid retrieval:** two branches fused inside **one Cypher `UNION ALL` query** — (1) BM25 fulltext with Lucene, query sanitization + Levenshtein fuzzy (edit distance 1 for terms ≥ 5 chars); (2) vector cosine similarity, 1536-dim default. Fusion is **CombMAX** (max, not RRF or convex combination) with a calibration factor and type-aware weights.
- **Graph traversal is NOT fused into scoring** — it is exposed as agent-initiated MCP tools: "they are agent-initiated navigation operations, not automatic signals fused into the scoring function."
- **Embeddings are a derived index, never authoritative:** "the vector layer never modifies belief state—an embedding cannot create, supersede, or deprecate a revision. The formal properties of Section 7 hold independently of whether embeddings are present, absent, or stale." A vector hit returns "a revision kref — a typed pointer back into the graph."
- **Client-side LLM reranking:** sibling revisions pre-filtered by cosine ≥ 0.30 (`text-embedding-3-small`), then the *consuming agent's own LLM* picks the best sibling "at zero additional inference cost" (three modes: client / dedicated / auto).

### 6d. Other capabilities

- **Dream State (async consolidation):** nine-stage pipeline that converts episodic → semantic; four LLM assessment outputs per memory (Relevance 0.0–1.0, Deprecation recommendation, Enrichment, Relationships). Safety guards: Dry Run, Published Protect, **Circuit Breaker (max 50% deprecation per batch)**, Error Isolate, Audit Report, Cursor Persist. Circuit-breaker threshold tunable 0.1–0.9. Explicitly disclaimed: "The nine-stage pipeline is an engineering contribution, not a formal one… we do not claim that the composition of a batch of such actions across multiple memories preserves all AGM postulates simultaneously."
- **Prospective indexing** (write-time enrichment): 3–5 LLM-generated hypothetical future scenarios indexed alongside each summary — the mechanism credited for bridging cue–trigger semantic gaps; "adding zero wall-clock time" (parallel via `asyncio.gather`, GPT-4o-mini).
- **Event extraction:** structured events + consequences appended to summaries to preserve causal chains.
- **51 MCP tools in 6 categories:** Cognitive Memory Lifecycle (`memory_ingest`, `memory_recall`, `memory_consolidate`, `memory_discover_edges`, `memory_store_execution`, `memory_dream_state`, `memory_add_response`); Working Memory (`chat_add/get/clear`); Graph Navigation; **Reasoning & Provenance** (`get_edges`, `get_dependencies`, `get_dependents`, `analyze_impact`, `find_path`, `get_provenance_summary`); **Temporal Operations** (`get_item_revisions`, `get_revision_by_tag`, `get_revision_as_of`, `resolve_kref`); Graph Mutation. Atomic writes: "A single `memory_ingest` invocation creates the complete memory unit."
- **Privacy architecture:** BYO-storage (metadata + pointers only; raw content local), local-first summary-to-cloud, PII redaction, multi-channel session identity, threat model.
- **Two reflexes:** "engage before responding, reflect after" — one MCP server, plugs into Claude Code, Codex, or your own stack.
- **Measured latency (their number):** "15 ms typical for working memory, 80–120 ms for long-term graph queries including hybrid search."
- **Reference implementation stack:** Rust gRPC server (tokio, tonic, neo4rs), Python SDK, Python MCP server, Flutter/Dart desktop, web dashboard with force-directed edge visualization.

### 6e. Notable gaps (their admissions + ours)

| Gap | Source |
|---|---|
| **No probability, no propagated credence, no Beta distributions, no uncertainty layer** | §7 — the formal logic is propositional/boolean by deliberate choice |
| **No contradiction edge type** — conflicts are only `Supersedes`; the system "does not automatically resolve the conflict" | §6.5, §8.6 |
| **`AnalyzeImpact` flags downstream beliefs but does not re-evaluate them** | Independent critique: Atlas paper — "Where Kumiho `AnalyzeImpact` returns the impacted set, Atlas `Reassess` re-evaluates each downstream belief"; Kumiho "explicitly defers automatic downstream reassessment as future work (§ 15.6)" |
| **Multi-agent asset-management pipeline validation is future work** | §1 contribution 1: "the asset management unification is an architectural contribution whose multi-agent pipeline validation is planned as future work" |
| **No system metrics** — "We do not report latency distributions, throughput measurements, or memory overhead per belief" | §15.9 |
| **Retrieval eval is anecdotal** — "The retrieval observations reported in this paper are anecdotal. A comprehensive assessment would require a larger query set (100+ queries) with multiple annotators" | §15.4, §15.9 |
| **No ablation study run** — events vs implications vs sibling filter is "planned" | §15.3, §16.1 |
| **Fusion hyperparameters uncalibrated** — calibration factor and type weights "have not been empirically optimized"; CombMAX chosen "argumentatively" and may be worse than RRF/convex combination | §8.2, §2.4 |
| **Consolidation quality is fully LLM-dependent** | §15.9: "Incorrect deprecation recommendations, despite safety guards, could degrade the memory graph over time" |
| **No eval of whether correct belief revision improves downstream agent behavior** | §15.9: "A benchmark that combines both… is the critical missing evaluation" |
| **Requires Neo4j** for CE | homepage FAQ |

*Last checked: 2026-09-11*

---

## 7. Go-to-Market & Acquisition

**Primary growth channels:**

| Channel | Activity |
|---|---|
| **Paper-first credibility** | arXiv preprint (Mar 2026) with **public compliance artifacts** — "the harness, results, and AGM compliance suite are committed to GitHub"; "The compliance report is committed and public"; "every number links to a public artifact you can re-run" |
| **Open-source funnel** | `KumihoIO` org (18 repos) + one-line CE installer + Claude Code plugin marketplace (`claude plugin marketplace add KumihoIO/kumiho-plugins`) + `pip install kumiho-memory` |
| **Desktop app as entry point** | "The easiest way in is Kumiho Desktop. One app installs Community Edition, connects to Kumiho Cloud, and adds plugins" — v0.5.0, auto-updating, signed macOS builds |
| **MCP standard distribution** | Places them in the same install path (MCP) as every other memory server; 51 tools |
| **Benchmark content marketing** | Blog posts with per-entry results and reproduction commands (e.g. the 93.3% LoCoMo-Plus post includes a full `git clone` + run recipe) |
| **Creative-industry origin** | VFX/animation/game studio asset management — Kumiho Browser, ComfyUI nodes, Ingest Studio |
| **Adjacent product** | Revka (`revka.ai`) as a separate runtime that *consumes* Kumiho memory — a second front door into the same graph |
| **Bilingual** | EN/KO site; Korean morphological search as a named feature (a real differentiator for the Korean market) |
| **Independent-adoption signal** | Atlas (third party) credits the paper by name: "the first publicly released implementation of Kumiho's specification" — free distribution they did not have to buy |

**Sales motion:** self-serve (CE free → Cloud Creator/Studio/Studio Pro) → Enterprise sales for governance/compliance/self-hosting. **Product-led, paper-backed**, with a strong OSS wedge.

**Key partnerships:** ⚠️ none found. No cloud marketplace listing, no named integrations partner, no VC announcement.

*Last checked: 2026-09-11*

---

## 8. Traction & Scale

**Scale framing:** very early. The evidence is technical credibility and shipping velocity, **not** adoption.

| Signal | Value |
|---|---|
| GitHub org followers (`KumihoIO`) | **7** (org created 2026-03-03) |
| GitHub stars — largest repo | **45★** (`KumihoIO/Revka`, 10 forks, Rust) |
| GitHub stars — memory repos | `kumiho-plugins` 2★ · `kumiho-server-community` 2★ · `kumiho-memory` 1★ · `kumiho-desktop` 1★ · `kumiho-SDKs` 0★ |
| GitHub stars — benchmark repo | **0★** (`kumihoclouds/kumiho-benchmarks`, 2 open issues) |
| CE release downloads | 13 total across 1 release (`v1.8.0`, 2026-09-08) |
| PyPI `kumiho-memory` | **72 releases**, latest v1.5.0 (2026-09-10), first 0.1.2 (2026-02-09) — shipping roughly weekly |
| PyPI download counts | ⚠️ unavailable (pypistats.org rate-limited at time of research) |
| Third-party reimplementation | **80★ / 20 forks** (`RichSchefren/atlas`, Apache-2.0) — larger audience than any Kumiho-owned repo |
| Named customers | ⚠️ **none published** |
| Funding / revenue / ARR | ⚠️ **no public data** |
| **Vendor-claimed benchmarks** (⚠️ UNVERIFIED, single-author vendor preprint; competitor scores sourced from *their* papers, not controlled re-eval) | LoCoMo token-level F1: **0.447 four-category** (n=1,540), **0.565 overall incl. adversarial** (n=1,986); adversarial refusal **97.5%** (n=446); LoCoMo-Plus **93.3%** judge accuracy (n=401, GPT-4o), **~88%** (GPT-4o-mini); recall accuracy **98.5%** (395/401); AGM **49/49** scenarios |
| **Independent reproduction** | LoCoMo-Plus **benchmark authors** reproduced "**results in the mid-80% range rather than 93.3%**" — disclosed by Kumiho in §15.3 and on the site ("Where a peak isn't reproducible on today's models, we say so — and don't quote it"). ⚠️ No published repro artifact located; this is Kumiho reporting someone else's number in *private correspondence* |
| **Independent citation** | ✅ **1 confirmed formal citation**: AWS Generative AI Innovation Center, *"Closing the Feedback Loop: From Experience Extraction to Insight Governance in Verbal Reinforcement Learning"*, **arXiv:2606.17591** (ICML 2026 RLxF Workshop), references "Park (2026) … arXiv:2603.17244", cited by its §2.2 and §2.4. Verbatim: **"Recent systems like Kumiho (Park, 2026) demonstrate the operational feasibility of these guarantees for agent memory"** and **"implementing AGM-compliant belief revision over graph-native memory architectures"** |
| Citation index | OpenAlex reports **cited_by_count: 0** for DOI `10.48550/arxiv.2603.17244` — likely indexing lag for an arXiv-only preprint (the AWS citation exists and is not counted). Semantic Scholar API was rate-limited at research time |
| Press / notable mentions | Asia/Korean tech summary (sns.style), an explanatory third-party blog (ranjankumar.in), a Moltbook commentary post, a self-run Reddit thread (r/KumihoIO). ⚠️ No mainstream tech press found |

**Independent reimplementation as traction (and as a threat):** `RichSchefren/atlas` describes itself as "the first publicly released implementation of Kumiho's specification," reproduces the 49/49 AGM suite (its `docs/AGM_COMPLIANCE.md` states: "This matches Kumiho's Table 18 result"), reuses the `kref://` URI scheme and all six Kumiho edge types — and then **extends past Kumiho** with a `Ripple` propagation engine giving **probabilistic downstream re-evaluation** (demo output: `0.88 → 0.75 (-0.13)`, `0.80 → 0.66 (-0.14)`). Atlas's paper also names Kumiho's stated gap: "Kumiho `AnalyzeImpact` returns the impacted set. They are not re-evaluated."

*Last checked: 2026-09-11*

---

## 9. Online Presence & Content

*Dev-tool adaptation — docs, papers, benchmark posts, engineering blog.*

| Metric | Value |
|---|---|
| Primary domains | [kumiho.io](https://kumiho.io/) (EN + KO) · [revka.ai](https://revka.ai/) · docs subdomain (`docs.kumiho.io` repo) |
| Paper | [arXiv:2603.17244](https://arxiv.org/abs/2603.17244) — 56 pages, 1 figure, submitted 2026-03-18, **CC BY-NC-ND 4.0** |
| Blog | Benchmark-anchored posts with reproduction commands, e.g. "93.3% on LoCoMo-Plus: How Kumiho's Graph-Native Memory Doubles the Best AI Can Do" (2026-07-17) |
| Docs quality | Referenced throughout ("Read the docs", "Full setup guide, including Windows", "See detailed limits"); ⚠️ not independently audited in this pass |
| Public proof artifacts | AGM compliance report, benchmark harness (`kumihoclouds/kumiho-benchmarks`), per-entry results, CE installers, desktop builds — all linked from the homepage |
| Content strategy | **"Paper → proof artifact → blog post → install command."** Unusual for a startup: every marketing claim is designed to be re-runnable. Explicitly drops non-reproducible peaks |
| SEO posture | ⚠️ not assessed (no SimilarWeb/Ahrefs access). Landing pages: Product, About, Use Cases (For Creators / For Studios / For AI Agents / For Developers), Resources, Blog, Enterprise, Pricing, Legal, Contact — a normal early-stage dev-tool surface, **no "vs competitor" pages** (unlike Zep's 6+ comparison pages) |
| Third-party coverage | [ranjankumar.in — "Why Agent Memory Needs a Graph: Lessons from the Kumiho Architecture"](https://ranjankumar.in/why-agent-memory-needs-a-graph-lessons-from-the-kumiho-architecture) · sns.style Korean summary · moltbook commentary · self-run r/KumihoIO thread |

**Distinctive content asset:** the **paper itself is the marketing**. The About page's credibility stack: "Published the formalism… Benchmarks run in public… Shipped production software used by teams… **Patent-pending architecture filed with the USPTO**."

*Last checked: 2026-09-11*

---

## 10. Community & Ecosystem

| Channel | Followers / Members | Engagement notes |
|---|---|---|
| GitHub `KumihoIO` | 7 followers, 18 repos | Repos pushed as recently as 2026-09-11 (daily activity from the company itself) |
| GitHub `KumihoIO/Revka` | 45★, 10 forks, 4 open issues | The most-engaged Kumiho-owned artifact |
| GitHub `kumihoclouds` | 8 repos, 0★ each | Release/plugin mirror org |
| PyPI (`kumiho-memory`) | 72 releases | `pip install kumiho-memory` is the documented non-Claude-Code path |
| Reddit | r/KumihoIO | Self-created subreddit; paper announcement post — ⚠️ engagement volume not captured |
| X/Twitter | `@KumihoHQ` (per GitHub org) | ⚠️ follower count not verified (x.com not fetchable in this pass) |
| Discord / Slack | ⚠️ **none found** | |
| Third-party ecosystem | 1 fork-family reimplementation (**Atlas**, 80★, Apache-2.0) + 1 integration ecosystem (ComfyUI, n8n, Claude Code plugin marketplace) | Atlas is the only independent developer community — and it competes |

**Community mechanics:** there is effectively **no community yet** — distribution runs through *standards* rather than social channels. MCP (any MCP client works), the Claude Code plugin marketplace, ComfyUI/n8n node ecosystems, and PyPI are the acquisition surfaces. The paper is the trust surface. Forks on company repos are 1 each, which reads as internal mirroring rather than external contribution.

*Last checked: 2026-09-11*

---

## 11. Customer Sentiment

**Sources checked:** kumiho.io (homepage, About, Pricing, FAQ, blog), GitHub issues/repo metadata, third-party blog analysis (ranjankumar.in), Atlas's independent paper and AGM compliance artifact, Reddit r/KumihoIO, web search for reviews/discussion.

**What users/customers praise:**
- ⚠️ **No user reviews found** — no G2, Capterra, Product Hunt, TrustRadius, or Hacker News thread located.
- Third-party *analysis* (not user sentiment) is positive on the architecture: ranjankumar.in frames Kumiho as the reference example of why agent memory needs a graph.
- The strongest external validation is a **citation, not a customer**: AWS's Generative AI Innovation Center cites Kumiho as demonstrating "the operational feasibility of these guarantees for agent memory."
- The most concrete external *adoption evidence* is Atlas choosing to build on (and credit) the spec.

**What users/customers complain about:**
- ⚠️ **No user complaints found** — consistent with having essentially no public user base.
- The sharpest technical critique found is **from a third-party implementer, not a customer**: Atlas's paper argues Kumiho's `AnalyzeImpact` only *flags* impacted downstream beliefs and never re-evaluates them, and that Kumiho "ships a commercial cloud service with thin open-source SDKs." Atlas positions its `Ripple` engine as the fix.
- The paper itself pre-empts the obvious criticism by naming the circularity: "**Self-evaluation bias.** The system is evaluated on its own deployment data during the authorship of this paper, creating an inherent circularity."

**Overall sentiment:** ⚠️ **Insufficient data.** No review corpus exists. What exists is (a) one independent academic citation, (b) one independent reimplementation that credits and then critiques the design, and (c) the vendor's own unusually self-critical limitations section. Sentiment cannot be rated.

*Last checked: 2026-09-11*

---

## Notes & Sources

**Primary sources (all retrieved 2026-09-11 unless noted):**
- **Paper (abs):** [arXiv:2603.17244](https://arxiv.org/abs/2603.17244) — "Graph-Native Cognitive Memory for AI Agents: Formal Belief Revision Semantics for Versioned Memory Architectures", Young Bin Park, 56 pages, submitted 2026-03-18, CC BY-NC-ND 4.0
- **Paper (full HTML):** [arxiv.org/html/2603.17244v1](https://arxiv.org/html/2603.17244v1) — **read in full** for this profile (all §7, §8, §11, §12, §15 quotations come from here)
- **Company site:** [kumiho.io](https://kumiho.io/) — homepage, taglines, benchmark table, desktop installers, FAQ
- **About page:** [kumiho.io/en/about](https://kumiho.io/en/about) — origin story, team description, "Receipts over adjectives", USPTO patent claim
- **Pricing:** [kumiho.io/en/pricing](https://kumiho.io/en/pricing) — all tier prices and limits
- **Benchmark blog:** [kumiho.io/en/blog/93-3-on-locomo-plus-…](https://kumiho.io/en/blog/93-3-on-locomo-plus-how-kumiho-s-graph-native-memory-doubles-the-best-ai-can-do) (2026-07-17) — per-category tables, cost breakdown, reproduction commands
- **Independent citation:** [arXiv:2606.17591](https://arxiv.org/abs/2606.17591) — "Closing the Feedback Loop: From Experience Extraction to Insight Governance in Verbal Reinforcement Learning", AWS Generative AI Innovation Center, ICML 2026 RLxF Workshop; reference list contains "Park (2026) … arXiv:2603.17244", cited by §2.2 and §2.4 (verified in the full HTML)
- **Independent reimplementation:** [github.com/RichSchefren/atlas](https://github.com/RichSchefren/atlas) (80★, 20 forks, Apache-2.0, created 2026-04-26) — `docs/AGM_COMPLIANCE.md` ("This matches Kumiho's Table 18 result"), `paper/atlas.md` (critique of Kumiho's flag-not-re-evaluate gap, `Ripple` confidence propagation demo output)
- **GitHub:** `api.github.com/orgs/KumihoIO` (created 2026-03-03, 7 followers, 18 repos, location "United States of America", Twitter `KumihoHQ`), repo metadata for Revka / kumiho-plugins / kumiho-memory / kumiho-desktop / kumiho-server-community; `orgs/kumihoclouds/repos`; `repos/kumihoclouds/kumiho-benchmarks` (0★); `repos/xjtuleeyf/Locomo-Plus` (37★, benchmark owner)
- **PyPI:** [pypi.org/project/kumiho-memory](https://pypi.org/project/kumiho-memory/) — 72 releases, v1.5.0 on 2026-09-10, first 0.1.2 on 2026-02-09
- **Benchmark provenance:** [LoCoMo-Plus paper (arXiv:2602.10715)](https://arxiv.org/html/2602.10715v1) / [ACL 2026 long paper](https://aclanthology.org/2026.acl-long.1150/) — Xi'an Jiaotong University; the benchmark Kumiho reports 93.3% on
- **Product #2:** [revka.ai](https://revka.ai/) — memory-native agent runtime, Merkle audit chain, verified status
- **Third-party analysis:** [ranjankumar.in](https://ranjankumar.in/why-agent-memory-needs-a-graph-lessons-from-the-kumiho-architecture)

**Gaps documented (not filled by inference):**
1. **No founders, no funding, no HQ, no team size** — Kumiho Inc. publishes none of it; no Crunchbase/Tracxn/press record found.
2. **Annual pricing not captured** — the pricing page defaults to monthly; the yearly figures require toggling a control not reachable via text fetch.
3. **PyPI download counts unavailable** — pypistats.org returned HTTP 429 during research.
4. **Semantic Scholar citation data unavailable** — API returned HTTP 429. OpenAlex reported `cited_by_count: 0`, which conflicts with the verified AWS citation (likely index lag for an arXiv-only preprint) — **the AWS citation is the reliable data point; treat the OpenAlex 0 as a lag artifact.**
5. **The "mid-80%" independent reproduction is not a published artifact** — it is Kumiho's own description of private correspondence with the LoCoMo-Plus authors.
6. **No peer review** — single-author preprint by an interested party, not accepted to a venue as far as could be found.
7. **No published peer/independent critique of the formal proofs.** The mathematical claims (AGM K\*2–K\*6 + Hansson postulates over a propositional fragment) were not independently verified in this pass; the weakness-avoids-Flouris argument is internally coherent but unrefereed.
8. **The asset-management half of the product has no published validation** — Kumiho says so itself.
9. **Score drift between paper and current site:** the homepage LoCoMo table shows Kumiho multi-hop **0.361** / overall **0.531**, while the paper reports multi-hop **0.355** / overall **0.565** (incl. adversarial). Also the blog states pre-enrichment >6-month LoCoMo-Plus accuracy was **43.8%**, while the paper states **37.5%**. ⚠️ Minor but real inconsistencies in a company whose positioning is "receipts, not adjectives."
10. **"Patent-pending"** is claimed with no patent number or filing date.

**Compression-claim caveat — CONFIRMED BY READING:** the 40×–280× token compression is measured by comparing stored **summary** tokens against the **estimated raw conversation** tokens that would otherwise be replayed (Table 11 + §15.1: "comparing the stored summary token count against the estimated raw conversation token count"). It is **not** a matched-token-budget comparison against retrieved passages of competing systems. The "250–400 tokens to recall k=5 vs 50,000+ to replay raw transcripts" framing is the same baseline. **The caveat in the research brief is correct as stated.**

*Last updated: 2026-09-11*
