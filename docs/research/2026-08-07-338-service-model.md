---
title: "Research: Migrate Tortoise from Library to Service Model (#338)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
created: 2026-08-07
aboutSubjects: tortoise
aboutObjects: tortoise, mcp-server, bsl-license
---

# Research: Migrate Tortoise from Library to Service Model (#338)

**Date:** 2026-08-07
**Status:** complete
**Depth:** deep (user-requested complex research)
**Domain:** product + engineering
**Issue:** #338 (migrated from daniel-ospina/eldato#7644)
**Sources:** 5 internal (codebase, docs, git, graph-scripts) + 17 external (Perplexity sonar ×7, Exa ×1 run, MongoDB docs)

---

## 1. Reframed Problem Statement

> **Daniel (Premise Labs)** is trying to get MIT-licensed products (David Waring) and new adopters to integrate Tortoise for agent memory, **but** the library-import model (`pip install tortoise` → import into your Python project) drags Tortoise's copyleft/source-available license into the adopter's own distribution, which blocks MIT-licensed products and creates adoption friction, **resulting in** lost integrations and a muddled "import vs. connect" story.

**Root cause (5 Whys):** License friction is not about BSL specifically — it's about *where the license boundary sits*. When a copyleft/source-available library is embedded via import, its terms bind the adopter's distribution. When the same engine runs as a service (self-hosted or hosted) and the adopter only connects over MCP/REST, the boundary sits at the network — the adopter's code never inherits the license.

**Alternative framings considered:**
- *"How might we keep Tortoise adoptable by MIT products without changing the engine license?"* → Service model + thin connectors.
- *"How might we make MCP the product and the SDK a power-user add-on?"* → MCP-first positioning.
- *Reverse:* "What if we made the SDK MIT and the service the paid layer?" (RecallWorks model — see §3.5.)

---

## 2. Internal Knowledge (Codebase + Docs + Git)

### 2.1 The hosted service ALREADY EXISTS — the gap is positioning, not build **[HIGH — direct codebase verification: hosted_api.py, fly.toml, infra-runbook, git log]**

Tortoise already runs as a service today:

| Asset | Location | Status |
|---|---|---|
| Hosted FastAPI platform (Fly.io) | `tortoise/hosted_api.py`, `Dockerfile.hosted`, `fly.toml`, `entrypoint.sh` | Live — api.premiselabs.co |
| MCP Streamable HTTP at `/mcp` | `tortoise/mcp_server.py` `create_http_app()` | Live (#236, #487) — tenant Bearer `tt_` keys, rate limit 100 |
| Tool registry (58 tools, programmatic) | `tortoise/tool_registry.py` | Live (#454) |
| CLI entry points | `pyproject.toml` `[project.scripts]` → `tortoise`, `tortoise-ingest`, `tortoise-serve` | Exists |
| Self-hosted docs + runbook | `docs/infra-runbook.md` | Live |
| Hosted onboarding epic | `docs/epics/2026-08-07-hosted-onboarding-235/` (research brief, scope, plan) | Draft |
| Multi-tenant REST API | `tortoise/hosted_api.py` (provisioning only; full REST = #7717) | Partial |
| Backups | FalkorDB Cloud (AOF + automated), R2 bucket | Live |

**But the README leads with the library:** Quickstart = `pip install -e .` first; "Hosted/self-hosted" are afterthoughts. `index.md` orders "SDK → MCP Server". The issue's Targets (#338) — README service-first, "Install → Connect → Query" docs, thin MCP/REST connectors — are exactly what remains.

### 2.2 ⚠️ License inconsistency — three files, three licenses (directly relevant to #338) **[HIGH — direct codebase verification: README.md, LICENSE, pyproject.toml, index.md]**

| File | Claims |
|---|---|
| `README.md` | "Business Source License 1.1" |
| `LICENSE` | **GNU AGPL v3** (with CLA note) |
| `pyproject.toml` | `license = "MIT"` |
| `index.md` | "AGPLv3 + CLA" |

The Tortoise graph already resolved this: `graph-scripts/decide_licensing.py` (archived 2026-08-05, re-filed to context `licensing-decision-compare`) evaluated three options — **AGPLv3 + dual-licensing (DEC-002, current) ranked 0.906 > BSL 1.1 on EP + AGPL on platform (Redis model) 0.8875 > SSPL 0.794** (ranking confirmed in `docs/plans/2026-08-05-remove-context-field-plan.md:451`).

**Implication:** The issue's "BSL was a blocker" framing refers to the *earlier BSL idea*. The actual current license is AGPLv3-dual. But the README's BSL claim is stale and must be fixed. Critically, **the service model resolves the David Waring objection under either license** — AGPL on a library you import is as much a blocker for an MIT product as BSL; AGPL on a service you connect to is not (network boundary). The issue is correctly scoped, but the "Why" should be updated to reflect AGPL, not just BSL.

### 2.3 Integrations today still embed SDK patterns **[HIGH — direct codebase verification: integrations/README.md, crm/twenty/bridge.py]**

- `integrations/meetings/minutes` — exposes 37 MCP tools (agent-facing; aligns with service model).
- `integrations/crm/twenty/bridge.py` — Python script using `requests` + direct Tortoise/FalkorDB access (library-style, not connector-style).
- `integrations/README.md` architecture diagram shows `bridge.py → Tortoise/FalkorDB` (direct), not `→ MCP/REST`.

### 2.4 Prior knowledge (epistemic memory) **[HIGH — direct verification: operations/memory absent; tortoise_client.py status command run]**

Epistemic memory checkpoint unavailable from this repo (`operations/memory/` not present; `tortoise/tortoise_client.py status` → "Tortoise SDK not installed"). Noted and skipped per protocol; graph context `licensing-decision-compare` recovered manually via `decide_licensing.py` archive.

### 2.5 Strategic Direction — User Decision (2026-08-07) **[HIGH — direct user direction, supersedes DEC-002 license recommendation]**

Daniel (product owner) has set the strategy for #338. This supersedes the licensing recommendation in the original DEC-002 graph decision (AGPLv3-dual) for *positioning purposes*:

1. **Dual offering, both first-class:** a low-priced hosted service (api.premiselabs.co) AND a self-hosted version. Not either/or.
2. **Segment logic:** solo devs → run locally (self-hosted service, MongoDB-style daemon); teams → need a server → hosted version. The hosted tier is the growth path; self-host is the trust path.
3. **Trust rationale:** self-hosting is a first-class feature, not a consolation — data locality, no vendor lock-in, auditability. "Run it locally, but as a service."
4. **Service model = interface, not deployment location:** the engine runs as a daemon (like `mongod`) and clients **connect** (drivers, connection strings, MCP) rather than import. The SDK's role becomes a thin driver/scripting layer — `pip install` stays for local dev but no longer embeds the engine in the consumer's app.
5. **License direction — two tracks:**
   - **Self-hosted version → BSL 1.1 with a revenue-threshold Additional Use Grant.** Free production use for organizations under the revenue threshold (e.g., $5M ARR); above threshold requires a commercial license. This serves the solo-dev/trust segment while protecting monetization.
   - **Hosted version (api.premiselabs.co) → commercial subscription, with a free tier.** Using the hosted service is NOT covered by the BSL free grant — it is a separate commercial product with its own tiers (free tier for solo devs / small usage, paid tiers scaling with teams). Same separation as MongoDB (self-host = software license; MongoDB Atlas = paid commercial service, freemium tier).

**Implication for the David Waring objection:** under BSL + revenue threshold + service model, an MIT-licensed product is never blocked — it connects to Tortoise (self-hosted daemon or hosted endpoint); the MIT product's own distribution never contains Tortoise code. The license boundary sits at the network, exactly as with MongoDB (server) + drivers (MIT/Apache).

**Supersession note:** this direction overrides the DEC-002 preference for AGPLv3-dual (0.906 vs BSL 0.8875). The AGPL-vs-BSL trade-offs from the graph research remain valid decision inputs (BSL: OSI non-recognition, distro exclusion, fork risk, enterprise legal clarity; AGPL: OSI-approved, network-copyleft, no countdown). The revenue-threshold AUG mitigates BSL's biggest criticisms — "open washing" is answered by genuinely free production use for small orgs, and the trust story is real, not a gimmick. **Action: file a superseding licensing decision to the graph when FalkorDB is back up (context: `licensing-decision-compare`), and update LICENSE/README/pyproject to BSL 1.1 + AUG.**

---

## 3. External Findings

### 3.1 BSL is standard for services but is not open source — and carries fork risk **[HIGH — internal graph + Wikipedia + LWN + OpenTofu + VictoriaMetrics + MariaDB + FOSSA]**

- BSL 1.1 is source-available, not OSI-approved. Production use is restricted unless granted; converts to an open license (usually Apache 2.0) after ~4 years (MariaDB, FOSSA, Wikipedia).
- Used by MongoDB, Elastic, Redis (later reverted), CockroachDB, Couchbase, HashiCorp, Sentry — all *service* software.
- MariaDB FAQ: a modified BSL work cannot simply be redistributed under MIT — confirming that BSL-licensed *libraries* poison MIT integration, while BSL-licensed *services* don't.
- Pushback is real: OpenTofu fork (HashiCorp), Valkey (Redis), OpenSearch (Elastic); VictoriaMetrics argues BSL "erodes trust"; LWN notes BSL is a non-starter for Linux distro inclusion; Sentry critiques variable Additional Use Grants (compliance complexity).
- Internal graph finding already weighted this: BSL fork-risk + OSI-gap were key differentiators vs AGPL in DEC-002.

### 3.2 MCP-as-primary-interface is a validated product pattern, with known production gaps **[HIGH — IBM + Scalekit + arXiv 2606.30317 + CSA + NSA + MCP roadmap]**

- Scalekit: MCP server is "a new entry point, not a new backend" — exposes the same backend APIs the UI already uses.
- arXiv 2606.30317 catalogs recurring MCP server patterns (Resource Gateway, Tool Orchestrator, Stateful Session Server, Proxy Aggregator, Domain-Specific Adapter).
- Clever Cloud: narrow capabilities, stable JSON schemas, least privilege, validation.
- **Production gaps (adversarial):** CSA "MCP Security Crisis" (widespread unauthenticated/publicly exposed servers); real-fault catalog (auth/registration/stability/concurrency); MCP roadmap admits Streamable HTTP gaps around horizontal scaling, stateless operation, middleware. Tortoise mitigates already: Streamable HTTP, Bearer auth, rate limiting, CORS allowlist.
- **Remote MCP auth is converging on OAuth 2.1** (RFC 9728 Protected Resource Metadata, RFC 8414, RFC 8707 resource indicators, PKCE, Dynamic Client Registration) **[MEDIUM — spec + multiple practitioner sources; adoption still young]**.

### 3.3 Self-hosted distribution: Docker-first, single container **[MEDIUM — community consensus (r/selfhosted, HN, docker guides); binary debate unresolved]**

- Docker images are the de-facto ideal for self-hosted distribution; install scripts breed dependency issues.
- Single-container ops model (one `docker run`, one port) is what agent-memory competitors converge on (Recall, mcp-memory-service, Kagura all ship single-container).

### 3.4 "Install → Connect → Query" is the canonical DB-as-a-service docs structure **[MEDIUM — MongoDB Atlas docs family + internal onboarding epic #235 already uses this framing]**

- MongoDB Atlas guides users: choose connection type → connect → copy connection string → query. Same flow in Supabase/Redis quickstarts.
- Our own onboarding epic (#235) already maps self-hosted→hosted onboarding around install/connect/query and found MCP config-snippet paste is the industry pattern (no guided flows exist beyond `claude mcp add`/`codex mcp add`).

### 3.5 Competitive convergence: agent-memory products are already service-first **[MEDIUM-HIGH — Exa discovery: mcp-memory-service, Kagura, Recall, AgentMemory, nwxio/mcp-memory]**

- **RecallWorks/Recall (2026):** MIT core + BSL for enterprise (multi-tenant/SSO/control plane). Single Docker image, MCP-native + plain HTTP, "Install it once, point your MCP client at it." **This is the exact license-split pattern that answers David Waring's objection while still protecting the hosted/enterprise layer.** ⚠️ single Exa discovery source — verify license-split specifics against Recall's repo/docs before Q1 decision
- **mcp-memory-service:** REST API (76 endpoints) + MCP transport + knowledge graph, Apache 2.0, self-hosted, local embeddings.
- **Kagura Memory Cloud:** 63 MCP tools + REST API + Next.js dashboard; self-hosted or cloud.
- **AgentMemory:** CLI + HTTP + MCP above pluggable providers; OAuth 2.1 DCR for remote MCP.
- **nwxio/mcp-memory:** MCP backend, SQLite/Postgres, local embeddings.
- **Takeaway:** No one ships "import our SDK" as the primary story. Every credible agent-memory product is "run the server, connect your client."

### 3.6 BSL revenue-threshold Additional Use Grant is a proven pattern **[HIGH — SPDX BUSL-1.1 text + Couchbase + MariaDB + HashiCorp + FSL/€5M examples + adversarial gotchas]**

- BUSL 1.1 §2: the licensor writes a **parameterized Additional Use Grant** (AUG) that may permit limited production use. This is the official hook for a revenue threshold (SPDX license text).
- **Real-world AUG precedents:**
  - Couchbase: production use permitted for any purpose except creating a commercial derivative work or commercial DBaaS/SaaS (competitive-services clause, no revenue number).
  - MariaDB MaxScale: free production use below **3 server instances** (quantitative threshold).
  - Sentry FSL (BSL variant): free production use under **~$5M annual revenue** (community-documented).
  - Published example: free production use while **total finances < €5,000,000** over trailing 12 months.
  - HashiCorp: production use allowed unless offering the work to third parties as a competing hosted/embedded service.
- **Standard BSL mechanics:** change date → converts to an open license (typically Apache 2.0) **4 years after each release**; the clock is version-specific (old self-hosted versions stay under original terms until their change date).
- **Adversarial — known gotchas (PowerPatent, MariaDB FAQ, community):**
  - Enforcement is hard; restrictions create customer confusion ("when do I need a license?") and operational friction at threshold crossing.
  - Enterprises that ban source-available licenses will still reject BSL (same class as AGPL-banning, but OSI approval is the difference).
  - Distro inclusion blocked during restricted period (LWN); community fork risk if users feel value-gated (Valkey/OpenTofu pattern) — mitigated when the threshold grants real free production use.
  - Version-specific change dates mean old copies stay restricted — must document which versions are under which terms.

---

## 4. Recommendation

### 4.1 Adopt the service-first repositioning (issue validated)

The internal build already did the heavy lifting (hosted platform, MCP-first tool registry, CLI, Dockerfile, runbook). Issue #338 is primarily a **positioning + connector + license-consistency** migration:

1. **README rewrite:** service-first. "Run Tortoise (hosted or self-hosted) → connect your tools (MCP) → query." `pip install` demoted to "SDK for local dev / scripting".
2. **Docs restructure to Install → Connect → Query** (mirror MongoDB Atlas):
   - *Install:* `docker run` one-liner (self-hosted) or sign up (hosted) — needs a published Docker image (GHCR) and/or `tortoise-serve` doc.
   - *Connect:* `claude mcp add tortoise https://api.premiselabs.co/mcp` / `codex mcp add` / `.mcp.json` snippet (already the #235 onboarding flow).
   - *Query:* MCP tools list (58) + REST reference (as #7717 lands).
3. **Thin connectors:** convert `integrations/crm/twenty/bridge.py` to talk to Tortoise over MCP/REST instead of direct FalkorDB/SDK; integrations README diagram updated to `→ MCP/REST`.
4. **License (resolved by owner, 2026-08-07): BSL 1.1 + revenue-threshold AUG for self-hosted; commercial subscription for hosted.** P0 hygiene: replace the inconsistent stack (README=BSL, LICENSE=AGPLv3, pyproject=MIT) with a single consistent BSL 1.1 + AUG license. Draft AUG below. File a superseding licensing decision to the graph (context `licensing-decision-compare`) when FalkorDB is up.

   **Draft Additional Use Grant (owner-approved 2026-08-07):**
   > Additional Use Grant: You may make production use of the Licensed Work for your own organization's internal purposes, provided that the total annual revenue of your organization does not exceed **US $5,000,000** (or equivalent in other currencies) in the most recent twelve-month period. This grant does not permit (a) offering the Licensed Work, or a substantially similar product, to third parties as a hosted or managed service, or (b) use of the Premise Labs hosted Tortoise service under these terms — the hosted service is licensed separately as a commercial product (free tier available).

   Change date: 4 years per release → converts to **Apache 2.0** (owner-approved 2026-08-07).
5. **Update issue #338 "Why"**: reframe BSL-specific objection → "copyleft/source-available license boundary moves to the network when Tortoise is a service" (works for AGPL today).

### 4.2 Open questions needing human decision

| # | Question | Status / Options |
|---|---|---|
| Q1 | License messaging | **RESOLVED (owner, 2026-08-07):** BSL 1.1 + revenue-threshold AUG for self-hosted; hosted = commercial subscription **with free tier**. Threshold = **$5M USD**; conversion license = **Apache 2.0** after 4 years. Draft AUG in §4.1. |
| Q2 | How far does #338 push packaging? | Docs/positioning only vs also publish GHCR Docker image + `docker run` quickstart (recommended — the local-daemon story needs it) |
| Q3 | Thin connectors: MCP or REST as the connector surface? | MCP (agent-facing, exists today) vs REST (script-facing, #7717 partial) vs both |
| Q4 | Is `pip install` kept as a documented path for local dev? | Yes — as a driver/scripting layer (MongoDB-driver analogy), not engine embedding |

### 4.3 Risks / gotchas

- **MCP production gaps:** scaling/statelessness behind load balancers, auth hardening (OAuth 2.1 roadmap), middleware — monitor as MCP spec matures.
- **License flip-flop risk:** README already drifted from the graph decision; whatever is chosen, docs must be wired to a single source of truth (the licensing decision Points in the graph).
- **Don't gut the SDK:** the service model does not mean deleting the SDK — it means repositioning it (scripting/local-dev power-user path), like MongoDB keeps drivers.

---

## 5. Source Confidence Summary

| Claim | Tier | Sources |
|---|---|---|
| Service model moves license boundary to network → resolves MIT/AGPL/BSL import friction | High | Internal (DEC-002 graph, issue), external (MariaDB FAQ, HashiCorp BSL, Recall license split) |
| BSL standard for services, source-available, fork risk | High | Internal graph + Wikipedia + LWN + OpenTofu + VictoriaMetrics + FOSSA |
| BSL revenue-threshold AUG is a proven pattern (Couchbase, MaxScale ≤3 instances, Sentry FSL <$5M, €5M example); gotchas: enforcement, threshold-crossing confusion, version-specific change dates | High | SPDX BUSL-1.1 text + Couchbase + MariaDB + HashiCorp + PowerPatent + FSL/€5M examples |
| Owner direction (2026-08-07): dual offering, self-host = BSL+revenue threshold, hosted = commercial; supersedes DEC-002 for positioning | High | Direct user decision (primary source) |
| Internal license decision = AGPLv3-dual 0.906 > BSL 0.8875 > SSPL 0.794; README/pyproject stale | High | decide_licensing.py + plan doc + LICENSE + README + pyproject |
| Hosted service + MCP-first infra already live (58 tools, Streamable HTTP, tt_ keys) | High | infra-runbook, hosted_api.py, fly.toml, git log #236/#487/#454 |
| MCP-as-product validated pattern; production gaps known (scaling, auth, security) | High | IBM, Scalekit, arXiv, CSA, NSA, MCP roadmap |
| Remote MCP auth converging on OAuth 2.1 (DCR, PKCE, resource indicators) | Medium ⚠️ emerging | MCP spec + 5 practitioner sources (spec, not yet mass adoption) |
| "Install → Connect → Query" canonical DBaaS docs structure | Medium ⚠️ emerging | MongoDB Atlas docs + internal #235 brief |
| Docker-first single-container self-host distribution | Medium ⚠️ emerging | Community consensus (Reddit/HN/docker guides) |
| Agent-memory competitors converge on service-first + MCP + REST alongside | Medium-High | Exa (5 independent projects) |

---

## 6. Sources

**Internal:** `README.md`, `LICENSE`, `pyproject.toml`, `index.md`, `graph-scripts/decide_licensing.py`, `docs/plans/2026-08-05-remove-context-field-plan.md`, `docs/infra-runbook.md`, `docs/epics/2026-08-07-hosted-onboarding-235/01-research-brief.md`, `tortoise/hosted_api.py`, `tortoise/mcp_server.py`, `tortoise/tool_registry.py`, `integrations/` (README + twenty bridge), `fly.toml`, `Dockerfile.hosted`, `entrypoint.sh`, git log (#236 #487 #454 #206).

**External:** hashicorp.com/bsl, mariadb.com/bsl-faq-adopting, fossa.com BSL guide, Wikipedia BSL, Couchbase BSL post, blog.adamretter.org.uk BSL adoption, LWN 2024-08-08, opentofu.org/manifesto, victoriametrics.com BSL blog, runtime.news HashiCorp analysis, sentry.io FSL post, IBM MCP architecture patterns, arXiv 2606.30317, clever.cloud MCP server design, scalekit.com enterprise MCP patterns, arXiv 2603.05637 (real MCP faults), cloudsecurityalliance.org MCP security crisis, nsa.gov MCP security PDF, modelcontextprotocol.io (spec/roadmap/authorization), mcp.directory OAuth 2.1, MongoDB Atlas connect docs, Reddit/HN self-hosting threads, SPDX BUSL-1.1 license text, powerpatent.com BSL/SSPL gotchas, mattrickard.com BSL, Exa: mcp-memory-service, kagura-ai/memory-cloud, RecallWorks/Recall, AndrewMoryakov/AgentMemory, nwxio/mcp-memory.
