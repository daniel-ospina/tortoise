<!-- pricing-tiers: canonical → owner-confirmed 2026-08-07 (YC application + cost-model + competitor analysis + feature-inventory revision) -->
<!-- supersedes: per-seat graph-filed decision (file_pricing_decision.py — historical) -->
<!-- cost model: FalkorDB Cloud @ $73/GB (conservative Startup rate); ~1KB/node + content-hash dedup -->

# Tortoise — Pricing Tiers

**Status:** current · **Owner:** confirmed 2026-08-07 · **Canonical machine source:** `product/pricing.json` (tiers + limits; pricing.md is the human-readable mirror)

---

## Pricing Tiers

| Tier | Price | Graphs | Collaborators | Included write ops/mo | Graph size (nodes) | API keys | Billing |
|------|-------|--------|---------------|----------------------|--------------------|----------|---------|
| **Free** | $0 | 1 | 1 | 1,000 | 10,000 (10MB) | 2 | — |
| **Solo** | **$9/mo** | 2 | 1 | 10,000 | 25,000 (25MB) | 5 | Per team |
| **Pro** | **$25/mo** | unlimited | **2** | 50,000 | 100,000 (100MB) | 10 | Per team |
| **Team** | **$149/mo** | unlimited | unlimited (invites + RBAC) | **200,000** | 600,000 (600MB) | 20 | Per team |

### Billing model

- **Billing is PER TEAM, not per user** — no per-seat charges anywhere. The tier is a property of the **Team** entity; limits are per team.
- **Multi-team is a USER capability, not a tier feature.** Any user may be a member of N teams (freelancer on their own Solo team + member of a client's Team team, in parallel). Each team independently selects its plan; team creation is rate-limited for abuse, not tier-capped.
- **Integrations are UNLIMITED at every tier** — more integrations (MCP servers, GitHub repos, connectors) mean more usage, which means more value. Abuse is controlled by a connection rate-limit, not by tier caps. (We meter the costly primitive — write ops + storage — and give away the hook.)

### Usage-based overage

- **$5 per additional 10k write ops** beyond the tier's included allowance (Pro + Team only).
- Metered on write operations (point/operator/session writes); reads (search, retrieval, context) are **free and unlimited**.
- Solo is a **hard-stop** (no overage) — it's the loss-leader that funnels to Pro.
- **How it reads on a bill:** Pro at 62K ops → 50K included + 12K overage (rounded to 2 units of 10k) × $5 = **$35 total**.

---

## Included Features by Tier

> Legend: ✓ = included now · **planned** = declared intent, future work (tracked, not built in this epic) · — = not included

### Free — $0
- 1 team · 1 graph · 1 collaborator · **unlimited integrations**
- 10,000 write ops/mo · 10,000-node graph · 2 API keys
- API access: REST (`/v1/*`) + MCP (`/mcp`, 58 tools)
- Supabase signup (GitHub OAuth / email) · key shown once on welcome
- Dashboard: overview, API keys, sessions
- Self-hosted option under the BSL grant (free <$5M revenue)

### Solo — $9/mo (loss-leader)
- Everything in Free, plus:
- **2 graphs** (the loss-leader cap) · **unlimited integrations**
- 10,000 write ops/mo (10× Free) · 25,000-node graph · 5 API keys
- **Usage dashboard (basic)** — see your ops consumption
- Hard-stop at limits (no overage) — upgrade to Pro to scale

### Pro — $25/mo (serves Power Users AND App Builders)
- Everything in Solo, plus:
- **Unlimited graphs** · **unlimited integrations**
- **2 collaborators** (owner + 1)
- 50,000 write ops/mo (5× Solo) · 100,000-node graph · 10 API keys
- **$5 per additional 10k write ops** — the scaling mechanism
- Standard support
- **The Pro feature story — "build multiple applications" (Supabase-Pro thinking):**
  - **Per-graph API keys** *(planned)* — isolate app A's key from app B's graph
  - **Daily backups + restore** *(planned)* — memory loss is fatal for an embedded app
  - **Usage dashboard** *(planned)* — ops consumed, overage runway, per-graph breakdown
  - **Webhooks on memory events** *(planned)* — apps react to new memories
  - **Data export** *(planned)* — JSONL, no lock-in
  - Capacity stays the *pricing* lever (ops/size = cost = overage); the feature story is **isolation + reliability + reactivity + observability**
- *The upgrade argument: "stop hitting caps" + "build on it, don't just use it."*

### Team — $149/mo (collaboration)
- Everything in Pro, plus:
- **Unlimited collaborators** — invites + **RBAC (owner/admin/member)**
- 200,000 write ops/mo · 600,000-node graph · 20 API keys
- **$5 per additional 10k write ops**
- **Audit log** *(planned)* — who did what (the collaboration trust feature)
- **Per-graph access (ReBAC)** *(future)* — per-graph visibility for agencies (account manager A → client 1's graph); the data model already supports it (Graph BELONGS_TO Team), access-policy layer only
- Priority support
- *The upgrade argument: "more people."* Invites, shared graphs, role-based access.

### All tiers
- API access: REST + MCP (58 tools) · tenant-scoped Bearer `tt_` keys
- **Unlimited integrations** (rate-limited for abuse, never tier-capped)
- Demo graph seeded on signup (idempotent, size-capped)
- Session keys (dashboard plumbing) — not a product limit
- Self-hosted: BSL 1.1 + $5M AUG, Mozilla Public License 2.0 (MPL-2.0) conversion in 4 years (see #338)

### Why hosted? (Vibecoder / low-technical segment callout)
- No setup, no servers — your agents just work
- Memory is backed up and managed for you
- Upgrade as you grow: Free → Solo → Pro → Team
- Self-host remains the sovereignty/control option (honest tradeoff: your infra, your ops)

### Display fields (mirror of pricing.json `display.*` — single source)
- **Monthly/annual:** annual default, **-20%** discount (`display.annual_discount_pct`)
- **Segments:** "Use Tortoise — memory for you and your team" · "Build with Tortoise — embedded memory via API for your product" (`display.segments`)
- **License line:** BSL 1.1 + $5M AUG, MPL-2.0 in 4 years (`display.license_self_hosted`)
- **Overage line:** "$5 per additional 10k write ops" · **Integrations line:** "Unlimited integrations at every tier" (`display.overage_line` / `display.integrations_line`)

---

## Competitive Pricing Analysis

**Date:** 2026-08-07 · **Method:** live pricing pages + web research · **Scope:** 6 competitors (Zep, Mem0, Langfuse, Letta, Honcho, Hindsight)

### The two pricing shapes in the market

| Shape | Players | Model | Unit metered |
|---|---|---|---|
| **Tiered (base + included + overage)** | Zep, Mem0, Langfuse, Letta, **us** | Low flat base (free–$30) + bounded included quota + per-unit overage | The vendor's costly primitive |
| **Pure usage (no subscription)** | Honcho, Hindsight | Pay-as-you-go per token/call | LLM tokens / calls |

### Competitor comparison table

| Product | Tier | Price | Included | Overage | Billing |
|---|---|---|---|---|---|
| **Zep** (closest — FalkorDB KG) | Flex | $125/mo | 50K credits, 5 projects | **$25/10k credits**, auto-top-up | usage credits |
| | Flex Plus | $375/mo | 200K credits | **$75/40k credits** | usage credits |
| **Mem0** | Starter | $19/mo | 50K add req, 1 project | none (hard stop) | fixed |
| | Pro | $249/mo | 500K adds, unlimited projects, graph memory | custom usage | fixed + custom |
| **Langfuse** | Core | $29/mo | 100K units | **$8/100k units** | usage units |
| | Pro | $199/mo | 100K units + compliance | $8/100k, volume discount | usage units |
| **Letta** | Pro | $20/mo | 20 agents | credits, auto-top-up | flat + usage |
| | API | $20/mo | unlimited agents | **$0.10/agent/mo + $0.00015/sec exec** | usage |
| **Honcho** | Pure usage | $0 base | **$100 free credits** | **$2.00/M tokens** ingested; retrieval free | usage tokens |
| **Hindsight** (Vectorize) | Pure usage | $0 base | credit packages ($10–$100) | **$10/M tokens** Retain · $0.75/M Recall · $0.05/call Reflect | usage tokens |
| **Supabase** (infra ref) | Pro | $25/mo | 8GB DB | $0.125/GB | usage |
| **Vercel** (infra ref) | Pro | $20/mo | 1M invocations | $0.60/1M invocations | usage |

### Total-cost curves (monthly, by write-ops volume)

Ours vs tiered competitors (exact):

| Monthly writes | **Us Pro $25** | Us Team $149 | Zep Flex $125 | Zep Flex+ $375 | Mem0 Pro $249 | Langfuse Core $29 |
|---|---|---|---|---|---|---|
| 50K | **$25** | $149 | $125 | $375 | $249 | $29 |
| 100K | **$50** | $149 | $250 | $375 | $249 | $37 |
| 200K | **$100** | $149 | $500 | $375 | $249 | $45 |
| 500K | **$250** | $299 | $1,250 | $937 | $249 | $61 |
| 1M | **$500** | $549 | $2,500 | $1,875 | $249+ | $101 |

Ours vs usage-based competitors (tokens→ops bridge, ≈300–500 tokens/write — MEDIUM confidence):

| Monthly writes | **Us Pro $25** | Honcho (~$6–8/10k equiv) | Hindsight (~$30–40/10k equiv) |
|---|---|---|---|
| 10K | **$25** | ~$7 | ~$35 |
| 50K | **$25** | ~$35 | ~$175 |
| 100K | **$50** | ~$70 | ~$350 |
| 500K | **$250** | ~$350 | ~$1,750 |

### Findings

1. **The norm is tiered (base + included + overage).** Zep, Langfuse, Mem0, Supabase, Vercel all follow "low flat base + generous-but-bounded quota + small per-unit overage." Free tiers hard-stop; overage exists on paid tiers only. We match this shape.
2. **Meter the costly primitive, give away the hook.** Zep charges 0 credits for retrieval/storage/users; Langfuse includes seats free. We meter **write ops + graph size** (our cost = storage + write throughput), keep reads free, and make **integrations unlimited** (they drive the usage we monetize).
3. **Curve validation — "cheaper to start, not a lot cheaper."** At 50K ops: $25 (us) vs $125 (Zep) vs $249 (Mem0). At 500K: $250 (us) vs $249 (Mem0) — **converges to market exactly.** The $5/10k overage was chosen because $2/10k made us 2–10× cheaper at scale.
4. **Zep is the closest architectural comparable** (FalkorDB KG) but prices 5× higher ($125/50K credits vs our $25/50K ops).
5. **Auto-top-up is the emerging agent-memory pattern** (Zep reloads at 20% balance; Letta configurable) — consider for the billing epic.
6. **Honcho's $100 free credits** is a conversion pattern worth considering for Free (optional).

### Competitive crossover analysis (where we stop being cheapest)

Beyond the included quota our per-op rate is effectively **$0.0005/op** ($5/10k) on both Pro and Team.

| Competitor | Their curve | Crossover point | Above crossover |
|---|---|---|---|
| **Honcho** | $0 base, ~$0.0007/op equiv ($6–8/10k) | ~35K ops/mo | We're cheaper (0.0005 < 0.0007) |
| **Mem0 Pro** (flat $249/500K) | $249 flat to 500K, then custom | ~400K ops (Team) / ~498K (Pro) | Mem0 cheaper (flat tier absorbs volume) |
| **Zep Flex $125** | $125 + $0.0025/op | never | We're cheaper at every volume (5×) |
| **Zep Flex+ $375** | $375 + $0.0019/op | never | We're cheaper at every volume (2×) |
| **Hindsight** | $0 base, ~$0.003–0.004/op equiv | never | We're cheaper at every volume (6–8×) |
| **Langfuse Core** | $29 + $0.00008/unit | n/a — different unit | Not directly comparable |

**Verdict (2026-08-07):** we stop being the cheapest option only at **~$250/mo of spend (~400–500K ops)** and **only against Mem0**. The crossover is narrow because graph-size caps bound the high-volume scenario and the affected cohort (top ~1–2%) is the least price-sensitive. **Decision: keep $5/10k for v1.** A volume-discount band is a later billing-epic review item.

### Our margin model (why the numbers work)

- **Cost basis:** FalkorDB Cloud at **$73/GB** (conservative Startup rate — planned at the worst case); ~1KB/node; `content_hash` dedup means repeated writes don't grow storage.
- **Margins at cap:** Solo 80% · Pro 71% · Team 71% (at the graph-size cap, the cost ceiling — once hit, no new writes).
- **Overage margin:** $5 per 10k ops vs ~$0.73 storage cost per 10k fresh nodes → **85%+ margin**; dedup'd writes (majority) are near-100%.
- **The graph-size cap is the cost ceiling:** worst case per user is bounded ($7.30/mo Pro, $43.80/mo Team at $73/GB).

---

## Historical reference (not current)

The wiped-graph decision (`graph-scripts/file_pricing_decision.py`): Pro $49/mo solo · Team $99/mo + $20/seat (per-seat). Superseded: per-seat conflicts with per-team billing; YC application prices are current; caps align to cost drivers (graphs/collaborators/ops/size), not seats.
