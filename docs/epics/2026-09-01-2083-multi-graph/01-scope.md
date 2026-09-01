---
title: "Epic #2083 Multi-Graph — Scope"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-01
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise-hosted-platform
---

# Epic Scope — Pro-tier multi-graph teams: per-graph API keys + graph provisioning

**Inputs:** `00-align.md` (PROCEED, 2026-09-01), `docs/research/2026-09-01-2083-multi-graph/research.md` (findings date 2026-09-01).

### Axis Research Notes

> **Findings date:** 2026-09-01. Fired for the UX axis (rated medium); Architecture/Isolation rated high — justified skip, boundary questions covered by the brief's Tech Stack Research section (FalkorDB ACL empirical repro, dual-mode gap, tenancy touch-map, provisioning/migration patterns). Findings appended to the brief's Raw Notes (2026-09-01 granular UX axis entry).

- **Quota/usage UX** — persistent per-resource counts near the thing being counted ("12 of 20 projects"), meters showing used+total, early warning at 80–90% with runway to act, the at-limit moment designed as an upgrade path not a wall, show usage before failure + name the consequence + offer CTA (upgrade or cleanup). Anti-patterns: usage buried three screens deep, warning only at the block itself, silent overage roll-in. (saasui.design 2026; dodo payments; userintuition)
- **Multi-project dashboard UX** — Supabase: resource-per-app model (separate project per unrelated app), a "Manage Databases" flow with an "Add Database" action, project-level roles (create/delete/update/pause), permission-aware switching. (supabase.com access-control docs)

## Scope Boundaries

### In Scope

1. **Dual-mode graph data model (W1):** `graphs` table + migrations in Supabase mode (graph_id, team_id, name, kind default|custom, namespace, status, created_at, quota-relevant fields); registry-mode Graph node already exists (sdk `_graph_create`) — keep in sync via the shared seam; `api_keys.graph_id` (nullable — NULL = team-wide key resolving to default graph) on both modes.
2. **Per-graph API keys + key scoping (W1/W3):** key minting scoped to a graph; `resolve_api_key` returns graph scope; key prefix scheme distinguishing team keys from graph keys; one-time plaintext reveal contract (hash-only storage already the pattern); key metadata (last_used, revoked_at, created_via extension).
3. **Provisioning endpoint + graph lifecycle (W2):** `POST /v1/teams/{team_id}/graphs` (mint → graph + per-graph key, 201, plaintext revealed once); graph list/delete/archive endpoints; naming/namespace derivation beyond `team_<id>` (existing pattern `team_{team_id}_{gid}`); per-tier quota gate on mint; default graph non-deletable guard.
4. **Provision capability + one-level-deep (W2/W3):** `provision: true` on team-bound keys only; graph-bound keys stamped `provision: false` by construction at mint (fixed child policy, never caller-supplied); delegation depth 0 for graph keys; revocation semantics (per-request opaque-key check → instant 401; append-only audit).
5. **Isolation enforcement across ALL surfaces (W3):** per-graph key → graph scope resolution on ask / analyze / search / MCP / sessions / context; app-layer registry scope check as the authoritative boundary (ownership check on every `select_graph`); FalkorDB ACL as defense-in-depth (per-graph ACL user recipe: `~tenant_<gid> +GRAPH.QUERY +GRAPH.RO_QUERY +PING`, deny GRAPH.LIST/KEYS/SCAN/CONFIG, secure default user, aclfile+ACL SAVE); cross-graph test suite.
6. **Tier gate (W1/W2):** provisioning gated to pro+ (`max_graphs_per_team` + tier check in both modes); free/solo remain single/two-graph.
7. **Delivery-shape alignment (W4):** `/v1/context` + `POST /v1/sessions` tenancy resolves against per-graph keys (graph-scope resolution added without breaking team-scoped callers — contract-compatible with #2080 D3).
8. **`session_recording` flag scoping decision + implementation (W4):** per-graph with team-level default (leaning, per #2082 Q5); decision recorded in the plan; keeps default-ON contract (#1927).
9. **Billing/quota + dashboard visibility (W5):** graph-count limits enforced per tier (soft warning at 80% → hard reject at cap; 402/409 semantics); dashboard shows graph list, per-graph keys, graph-count usage; quota source built for Supabase mode (the missing piece today).
10. **Migration + docs (W6):** primary/default graph pattern (existing team keys resolve to default graph; no forced migration, no key rotation); default graph = graph 0; registry-graph-schema.md + auth-architecture.md + pricing.json updates; migration reversible (documented + rollback path).

### Out of Scope

- **Fine-grained Principal/Scope/Capability policy layer** (agents, read/write asymmetry, statement-level provenance, per-user-inside-graph) → #2082 design, later epic. This epic is the coarse wall only.
- **Per-user-inside-graph isolation** — replaced by provisioning-to-graph for the developer-customer path.
- **FalkorDB instance-per-tenant escalation** (separate instances per customer) — the max-security tier; not this epic. Documented escalation path only.
- **Per-graph billing lines** — metering stays team-level write_ops (research: nobody charges per-graph; count is a tier gate, usage is the meter).
- **Cross-team / cross-org graph sharing** — graphs belong to one team; no graph-sharing grants.
- **Dashboard redesign beyond graph/key/quota visibility** — no unrelated dashboard work.

### Boundary Rationale

The cut follows the product decision + research: **graph = isolation boundary, key = access credential, policy = coarse (provision) only**. The app-layer registry scope check stays the authoritative boundary (FalkorDB ACL is defense-in-depth — leaks documented #2652, so it cannot be the wall). Everything in scope serves one of: create a graph (provision), bind a key to a graph (scoping), enforce the boundary (isolation), gate by tier/quota, or keep the existing product working (migration, contract compat). The fine-grained policy layer is explicitly deferred to #2082 because this epic's isolation story is complete without it (one graph = one key = one tenant).

## Customer Value Map

| Scoped Capability | User-Visible Value |
|-------------------|--------------------|
| Provisioning endpoint (mint graph + per-graph key) | A developer creates an isolated memory graph for an end-customer in one API call and gets its credential — no human provisioning |
| Per-graph API keys | Each end-customer's memory is guarded by its own key; one customer's data is never readable with another's key |
| One-level-deep provision capability | Developers can mint customer graphs safely; a leaked customer key can't mint new graphs or escape its tenant |
| Cross-graph isolation enforcement | Customers' memories are isolated at the data boundary — a key that touches another graph is denied, verified by test |
| Tier gate (pro+) | Multi-graph is a pro/team feature — free/solo tiers keep their limits, the upgrade path is clear |
| Graph lifecycle (list/delete/archive) | Developers manage customer graphs at scale — see what exists, retire what doesn't |
| Quota enforcement + dashboard visibility | Teams see their graph-count usage before hitting the cap; no surprise 402s |
| `/v1/context` + sessions per-graph tenancy | Agent hooks and session capture run against the right graph automatically — no cross-customer context bleed |
| `session_recording` per-graph with team default | Recording can be tuned per customer graph without per-team overhead |
| Default-graph migration (graph 0) | Existing teams and keys keep working untouched — no forced migration, no key rotation |

## Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| UX | medium | Dashboard graph list + per-graph key management + quota visibility follows established multi-project dashboard patterns (Supabase precedent); API-level UX follows one-time-reveal + prefix conventions |
| Architecture | high | Dual-mode data model (Supabase graphs table + registry sync), per-graph key resolution on EVERY request path (~20 `_make_sdk` sites + MCP + sessions/context), isolation enforcement with ACL defense-in-depth, provisioning + migrations |
| Ontology | low | Graph entity + team→graph 1:N already in the registry schema; adds `api_keys.graph_id` scope + `provision` capability attribute — no new entity classes |
| Accessibility | low | Dashboard additions follow existing dashboard components/patterns; no novel interaction model |

## High-Level E2E Test Cases

### E2E-1: Provision a graph with a per-graph key
**Given:** a pro-tier team with a team-scoped key carrying `provision: true`
**When:** the developer calls `POST /v1/teams/{team_id}/graphs` with a graph name
**Then:** a graph is created (status active) with a derived namespace, and the response contains the graph metadata + a per-graph API key whose plaintext appears exactly once (`revealed_once: true`)
**And:** the returned per-graph key reads/writes the new graph successfully (write a point, read it back)

### E2E-2: Per-graph key isolation — cross-graph denial
**Given:** team T with graphs A and B, and keyA bound to graph A
**When:** keyA attempts any read/write operation against graph B (ask, analyze, search, MCP, direct SDK, sessions, context)
**Then:** the operation is denied at the boundary with an auth/scope error — keyA can never touch graph B's data
**And:** the same holds with the data-layer ACL: a graph-A-scoped FalkorDB credential gets NOPERM on graph B (verified separately — defense-in-depth)

### E2E-3: Tier gate — provisioning is pro+
**Given:** a free-tier team (max_graphs=1) and a solo-tier team (max_graphs=2), each with a team key
**When:** either team attempts to provision beyond its tier limit (free: a 2nd graph; solo: a 3rd)
**Then:** the mint is rejected with a quota/tier error (402/409), and the team can see its graph-count usage in the dashboard

### E2E-4: One-level-deep — minted graph keys cannot provision
**Given:** a graph key minted by provisioning (graph-bound, `provision: false` stamped at mint)
**When:** that graph key calls the provision endpoint
**Then:** the call is denied (403) — graph-bound keys can never mint child graphs, in either control-plane mode

### E2E-5: Existing-team migration — default graph keeps working
**Given:** a team that existed before this epic with a team-scoped key and data in its default graph
**When:** the epic ships and the team uses its existing key (no changes)
**Then:** the key still reads/writes the default graph (graph 0) — no migration action, no key rotation, no data move
**And:** the default graph is not deletable

### E2E-6: Delivery-shape tenancy — context + sessions resolve per graph
**Given:** a per-graph key for graph A
**When:** the key calls `GET /v1/context` and `POST /v1/sessions` (with `session_recording` inherited from the team default)
**Then:** both resolve to graph A's memory — context is graph-A scoped, session points land in graph A, and cross-graph context is never surfaced

### E2E-7: Quota + revocation lifecycle
**Given:** a pro team at the soft-warning threshold of its graph-count quota
**When:** the team provisions one more graph, then revokes a graph key
**Then:** the team sees a soft warning at 80% before the cap, is rejected with a clear quota error at the cap (not silently billed), and a revoked graph key immediately returns 401 on the next request in every surface

## Epic Scope Ready for Review

**Scope:** 10 in-scope capabilities (dual-mode graph model, per-graph keys, provisioning, provision capability + one-level-deep, isolation across all surfaces, tier gate, delivery-shape tenancy, session_recording scoping, quota + dashboard, migration/docs) — fine-grained policy (#2082) and per-graph billing explicitly out.
**Customer value map:** 10 capabilities mapped to user-visible outcomes.
**E2E test cases:** 7 drafted (provision, isolation, tier gate, one-level-deep, migration, delivery-shape, quota/revocation).
**Complexity:** UX medium · Architecture high · Ontology low · Accessibility low.

Review the scope boundaries, customer value map, and E2E test cases.
Reply **"proceed"** to continue to detailed planning (test-design gate + epic-plan), or give feedback.
