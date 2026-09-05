---
title: "Epic #2083 Multi-Graph — Strategy Alignment"
type: decisions
domain: platform
doc_status: live
created: 2026-09-01
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise-hosted-platform
---

# Strategy Alignment Decision

**Feature:** Pro-tier multi-graph teams — per-graph API keys + graph provisioning (developer-customer path)
**Decision:** **PROCEED** (owner-validated product decision, 2026-09-01)
**Issue:** #2083 (complexity:complex, team:epistemic-team)

## Grounding facts (verified 2026-09-01)

- **Product decision already made (Daniel, 2026-09-01):** pro tier and above get multi-graph teams; API keys are per-graph; provisioning via key capability returns graph + key. Developer-customer model: each app end-customer = one graph = one key = isolated memory.
- **Pricing is already aligned:** `product/pricing.json` lists `per_graph_keys: "planned"` for **pro ($25)** and **team ($149)**; pro segments include `app_builder`. `max_graphs_per_team`: free=1, solo=2, pro/team=null (unlimited).
- **Registry schema already has the Graph entity:** `docs/registry-graph-schema.md` documents Team (incl. `max_graphs`), APIKey (BELONGS_TO **Team** — the gap), Membership, Invitation.
- **SDK infrastructure exists but is TWO-MODE, and the delta differs per mode:**
  - **Registry mode (selfhost):** `tortoise/sdk.py` has `_graph_create` (registry Graph node, team→graph 1:N), `graph_list`, `graph_count`; custom namespace shape `team_{team_id}_{graph_id}`. The `Graph` registry entity already exists.
  - **Supabase mode (hosted — the mode the developer-customer path ships to):** **NO graphs table** — `graph_metadata` (supabase_control.py) derives only the default graph from `teams.graph_name` and explicitly does NOT list custom graphs; `api_keys` has **no `graph_id` column** (verified: no graph_id in supabase/migrations); `graph_count()` queries the registry graph unconditionally and is therefore not enforced in hosted mode (registry empty by design under the #765 zero-registry-writes contract). The Supabase data model (0006–0009) has no graphs table at all.
  - **Net:** the *registry-mode* Graph entity does not translate to hosted mode — provisioning, per-graph key binding, and quota counting each require new persisted Supabase tables/columns + migrations. Build cost is lower than greenfield (SDK seams, namespace derivation, registry-mode patterns all exist) but materially higher than the registry-mode read alone suggests.
- **Tenancy today is team-scoped:** `resolve_api_key` → team_id + limits; MCP carries `_current_team_id`; API key → team → default graph. **No per-graph key resolution exists** in either mode.
- **#2082 (auth research) principle:** "graph = isolation boundary, authorization policy = access boundary" — FalkorDB native multi-graph + per-graph ACL as the coarse wall. This epic is the coarse wall; #2082's Principal/Scope/Capability layer is explicitly OUT of scope for #2083.

## Alternatives considered

1. **Do nothing (keep one-graph-per-team)** — cheapest; zero new surface. Rejected, but honestly: team-per-customer with team-scoped keys **is technically possible today** (pricing.json grants multi-team membership; keys isolate teams from each other). The rejection is *scalability + product shape*, not impossibility: rate-limited team creation, key sprawl, no provisioning API, no per-graph isolation story — a developer building a multi-customer app must hand-provision a team+key per customer by hand, which doesn't scale and isn't the product. "Each end-customer = isolated memory" is the app_builder product story, and per_graph_keys is already advertised in pricing.json as "planned".
2. **Multi-graph with team-wide keys only (no per-graph keys)** — cheaper (no key-scoping work). Rejected: a team-wide key that can touch any graph defeats the "each customer = one key" security story; the isolation guarantee the customer buys is the per-graph key. Would require a v2 migration later — build it once, correctly.
3. **External tenancy (one FalkorDB instance/namespace per customer via a multi-tenant proxy)** — Rejected: cost-prohibitive and operationally heavy; FalkorDB native multi-graph already exists in the stack, and #2082's exploration explicitly says the graph is the isolation boundary, not the permission boundary.
4. **Build it (chosen)** — the SDK-side Graph entity (registry mode), custom namespaces, and Supabase control-plane seams already exist and de-risk the work, BUT the hosted path additionally requires: a `graphs` table + `api_keys.graph_id` column (+ migrations) in Supabase mode, per-graph key resolution in `resolve_api_key`, a provisioning endpoint, and a real quota-count source in hosted mode. The real delta: **per-graph key scoping + provisioning + isolation enforcement + tier/quota (incl. hosted-mode data model) + delivery-shape tenancy**.

## Anti-post-rationalization (strongest reasons NOT to build)

- **No paying customer has demanded it.** The hosted platform is still beta; the current base is power users, not app builders. The demand assumption rests on a segment in pricing.json, not on revenue.
- **Isolation is a security-critical surface.** A per-graph key that leaks cross-graph = customer A reads customer B's memories = a trust-killing data-safety incident. The cross-graph test suite is a mandatory gate, not a nice-to-have, and "right first time" is the only acceptable bar.
- **Complexity is honest.** `complexity:complex` — the epic touches every request path (ask / analyze / search / MCP / sessions / context), two control-plane modes (registry + Supabase), billing/quota, and the delivery-shape tenancy contract. Surface area is the risk.
- **Migration burden is real.** Existing single-graph teams and existing team-scoped keys need a coherent story (default graph resolution, key migration). A half-done migration strands current users — the #1686/#1748 registry invariants (no orphaned teams/graphs) are already load-bearing.
- **Opportunity cost.** Graph count is not what blocks revenue today. Why-aware recall (#2080) and write-path evals serve the existing base. Running both in parallel (as this epic declares) is the mitigation — but #2083 consumes engineering that could otherwise land #2080's headline feature sooner.

## Opportunity cost

The alternative deployment of the same engineering: **#2080 (gbrain learnings — why-aware recall + write-path evals)**. Both are scheduled in parallel and do not conflict (this epic depends on nothing; consumes #2082 design decisions when frozen). The gbrain work targets recall quality for the existing power-user base; #2083 targets a *new* revenue segment. Neither blocks the other; the parallel-run decision stands.

## Profit growth alignment

**Causal chain:** per-graph keys + provisioning → developers build apps on Tortoise where each end-customer gets an isolated memory graph → Tortoise becomes an embeddable memory backend (aligns with #2080's embeddable delivery-shape direction) → new revenue surface: per-graph quota/overage and usage that scales with the developer's customer count, plus pro→team upgrades for multi-graph teams.

- **The volume multiplier is the structural win — with the pooling caveat:** graph count tracks the developer's *customer count*, not our marketing spend. A developer with 100 app customers = 100 graphs = usage that grows with their revenue. Caveat (post-review): billing pools write_ops **per team** (50k included on pro, overage $5/10k on the team pool), so the monetization is indirect — more customer graphs drive more team-level write_ops and retention on pro/team; graph count is the *driver of scale*, not a per-graph billing line. The graph-count quota itself is the pacing mechanism (teams buy up / hit overage as they grow).
- **Near-term expectation (6 months, beta):** $0–$100s/mo direct — no paying developer-customer yet; value is the strategic option, not current revenue.
- **Upside case (12–24 months, real developer adoption):** $1000s/mo from graph-scaled usage + tier upgrades. Flagged as upside, not forecast.
- **Cost side:** isolation enforcement is one-time engineering; FalkorDB multi-graph is native (no per-instance cost); marginal infra cost per graph is small (nodes in one instance). The expensive part is enforcement correctness, which is also the product.

## Eisenhower placement

**Schedule (Important / Not Urgent) — with the counter-argument named.** Important: unlocks the developer-customer segment (explicitly sold in pricing.json as "planned"), is a security/isolation prerequisite for any multi-tenant deployment, and the product decision (2026-09-01) makes it a committed platform capability. Not urgent: no paying app_builder customer has signed up against the advertised feature, the platform is beta, and graph count does not block current revenue. **Counter-argument (post-review):** the feature is advertised to the app_builder segment in pricing.json, so urgency is real if a pro customer signs up against it — that argues for a Do-now tilt on delivery speed once it starts, not for reclassifying the epic. The placement is Schedule with the caveat that the advertised-feature commitment converts it to Do-now the moment an app_builder signs up; the plan should therefore sequence the isolation-critical work early even though the epic itself is not a fire.

## Key assumptions

- **Developer-customer demand will materialize (app_builder segment)** — confidence: **medium** (segment listed in pricing; zero paying demand to date).
- **FalkorDB native multi-graph + per-graph ACL meets the isolation bar** — confidence: **medium-high** (native capability; per-graph ACL mechanics + username/ACL routing must be verified in research; fallback is registry-scope enforcement).
- **Existing SDK Graph infrastructure is sound and extendable** — confidence: **high** (verified: `_graph_create`/`graph_list`/`graph_count` + registry Graph node + `supabase_control.graph_metadata` + custom namespace derivation).
- **Team-scoped keys migrate to per-graph scoping without breaking existing users** — confidence: **medium** (needs a coherent default-graph resolution story; open question in scope).
- **Graph-count billing limits are enforceable in BOTH modes** — confidence: **medium**. In registry mode `max_graphs` + `graph_count()` exist; in Supabase mode there is **no graphs table and no Supabase branch in `graph_count`** — the quota source must be built (graphs table + enforcement) before this assumption holds. This is a real delta, not a lift-and-shift.

## Recommendation

**PROCEED.** The product decision is made and priced; the SDK-side graph infrastructure largely exists (registry Graph entity, custom namespaces, control-plane seams); the delta is per-graph key scoping, provisioning, isolation enforcement, quota, and delivery-shape tenancy — complex but bounded. The security-critical nature of cross-graph isolation mandates a dedicated cross-graph test suite as a hard gate, and the migration path for existing teams must be designed before shipping (scope-stage open question). Parallel execution with #2080 stands — the epics do not conflict.

**Why not DEFER?** The isolation capability is a prerequisite for the developer-customer path; the owner has decided; pricing already advertises it as planned; and the existing SDK seams mean deferring saves little while delaying the strategic option. The honest risk is demand timing — mitigated by the small marginal cost of shipping the capability before demand proves out. **Falsification instrument (added post-review):** track pro+/app_builder team creation per quarter; if developer-customer graph provisioning shows < N teams in 6 months post-ship, revisit the priority. Demand is the medium-confidence assumption the instrument watches.

**Routing:** PROCEED → `epic-research` (Stage 2). Research brief must verify: (a) FalkorDB multi-graph + per-graph ACL mechanics and ACL-vs-registry enforcement options, (b) the current tenancy resolution surface (every request path that resolves key→team→graph) in BOTH control-plane modes, (c) the #765 zero-registry-writes contract and the Supabase graphs-table data-model gap (0006–0009) as the hosted-mode delta, (d) #2080 D3 / delivery-shape `/v1/context` + `/v1/sessions` tenancy contract, (e) migration patterns for existing single-graph teams + existing team-scoped keys, (f) pricing/quota model options for graph count (note: write_ops pool is per-team, so graph-count→revenue mapping is indirect).
