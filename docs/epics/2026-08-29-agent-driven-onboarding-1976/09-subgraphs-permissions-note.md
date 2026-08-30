---
title: "Design Note — Subgraphs & Permission Axes (conceptual exploration, NOT in scope)"
type: synthesis
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-29
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Design Note — Subgraphs & Permission Axes (conceptual)

> **Status:** exploration only. The user is exploring (a) the team path (invite humans), (b) permission management (per-actor scopes on the graph — humans AND agents), and (c) subgraphs with different access axes — CONCEPTUALLY, separate from this epic. NOTHING here is in scope for #1976; this note records the research + the cheap future-proofing pins so the design doesn't foreclose later options.
> **Research:** sub-agent dispatch (fresh context, 2026-08-29) — how knowledge-management products + knowledge-graph systems handle subgraph partitioning and per-subgraph access axes. Full brief below; sources at end.

## Current design baseline (verified)

- **One FalkorDB graph per org**: `graph_name = f"team_{team_id}"` (hosted_api.py). Org = the single tenant boundary + namespace.
- **No subgraph concept exists.**
- **Permission management explicitly deferred** (epic W10/RBAC: "who can read/query what, data-access tiers per member").
- Actors (humans + agents) are Subjects in the graph — uniform actor model (ONTOLOGY: Subject = "any entity that can act").

## Research findings (condensed)

### Dominant model: container hierarchy with permission inheritance
Notion (workspace → teamspace → page → row), Confluence (global → space → page), Obsidian (vault) all partition by container with permissions inherited down the tree — a **single access axis** ("who can see/edit this container and everything under it"). Agent-memory products (Mem0, Letta, Zep, Fabric, MemFabric) scope by **identity fields** (user/agent/app/session) at the storage layer — Mem0 is explicit that "scoping ≠ permissions; role logic lives in the app layer."

### Notable exceptions (closest to the user's question)
- **Confluence spaces**: every space carries an independent permission set — effectively per-subgraph ACLs in a mainstream product.
- **Dust**: two-layer model — agents get their OWN data-access scope (per space) independent of human access; "each agent belongs to exactly one space and can only access data from that space." The sharpest articulation of agent-vs-human access divergence (HR-agent case: agent may legitimately exceed user access).
- **Mem (two philosophies)**: mem.ai keeps the entity graph GLOBAL by design, spaces are *retrieval lanes* (focus, not isolation); Mem[v] gives each space its own fully-isolated graph. Directly relevant: two viable answers to "subgraph with its own access axis."
- **Letta**: shared memory blocks are block-level read-only, NOT per-agent ("can't make a block read-only for some agents and writable for others") — a cautionary gap.

### Agent-specific access
- **Mem0**: four orthogonal scoping dimensions (user_id, agent_id, app_id, run_id) enforced at storage layer; graph entities built *within* a scope.
- **Dust principle**: "a user should never retrieve through an agent what they couldn't access directly" — yet agents may legitimately exceed user access (HR case).
- **Letta Code**: cross-agent memory guard (hard-denies access to another agent's memory unless scope allows).
- **Claude Code/Cursor**: memory is per-project; no cross-project sharing by default.
- **MemFabric**: (scope, scope_id) pairs with recall allowlists; explicit "Scopes are not security — your application decides which scopes a caller may pass."

### Schema/implementation patterns (graph DBs)
- **Named graphs** (FalkorDB SELECT GRAPH, GRAPH.LIST): cheap physical partitions — BUT relationships cannot span graphs (Neo4j), so split-by-graph only works for DISJOINT subgraphs. Tortoise's EP propagation must not cross subgraph boundaries within one graph.
- **Property/label partitioning**: tenant-by-label in a shared graph + query-time filtering (Mem0 identity fields, Graphiti group_id, MemFabric scope allowlist).
- **Node/edge ACLs**: Neo4j offers label/rel-type/property combos (DENY READ/TRAVERSE) but warns **property-based access control adds significant performance overhead**; ACL-relationship patterns have traversal bottlenecks at scale.
- **Provenance-based access**: Zep projects source metadata onto facts (ABAC); Collaborative Memory paper gives every fragment immutable provenance (creator, agents, resources, timestamps) for retrospective permission checks — matches Tortoise's existing authoredBy/event-log design.

### The "axes" concept
- **Orthogonal axes per subgraph is RARE.** Closest: Collaborative Memory paper (arXiv 2505.18279) — read AND write policies independently configurable at system/agent/user scope over two bipartite permission graphs (user↔agent, agent↔resource) — visibility axis + agent-access axis, time-varying. ABAC (Cerbos, arc42) allows multi-attribute rules incl. time windows.
- **Retention axis**: no mainstream product does per-subgraph retention (Slack/GWorkspace are org-level). Would be a Tortoise differentiator; must be stored as metadata (Graphiti bi-temporal valid_at/invalid_at).

## Design implications (cheap future-proofing pins — NO scope change)

1. **Treat `graph_name` as a namespace key, not an access domain.** Nothing in the SDK should assume "one graph = one ACL domain." Keep subgraph as an *optional* dimension on reads/writes. (This is the line added to W2 #1998.)
2. **Cheapest future path = property-based subgraph scoping INSIDE the one org graph**, NOT per-subgraph named graphs (named graphs sever cross-subgraph edges + EP propagation). The existing `MemoryScope.filter(team_id, memory_types)` protocol is already a proto-subgraph axis — `memory_types` is effectively "decisions-only vs sources-only" today. Extending to a `subgraph_id`/scope field (default org-wide) makes per-subgraph ACLs a later policy-map + query filter, not a migration.
3. **Store provenance on every node/edge now; store security scope on GOVERNING CONTAINERS (inheritance down) with node-level overrides only for exceptions** (creator actor, org, memory type, subgraph_id, validity window). Immutable provenance is sufficient for RETROSPECTIVE permission/retention checks — no ACL machinery needed until W10/RBAC. (R1 from ChatGPT comparison: not scope-on-every-node.)
4. **Don't bake ACLs into the graph** (no node-level ACL edges, no per-property permission predicates). Neo4j's own docs warn property-level ACLs add significant query overhead; Mem0/MemFabric/Dust converge on storage = scoped, enforcement = API layer. Tortoise's MCP/server layer is the right enforcement point.
5. **Defer but don't foreclose**: whether subgraph access is additive (most-permissive-wins) or deny-override — pick in the RBAC phase; the data model should not presuppose inheritance.
6. **Keep the Dust split in mind**: agent data-access scope ≠ human visibility scope is the feature agents need. Tortoise's actor model (humans + agents as Subjects) can hold both — don't collapse them into one permission set later.

## Comparison: ChatGPT "Designing Agent Memory Permissions" (shared chat, 2026-08-29)

> User asked: does this conversation conflict with the epic design? Source: https://chatgpt.com/share/6a931695-aca0-83e8-8211-d1aa89ab8446 (non-canonical, AI-generated — treated as design input, not evidence).

**Verdict: NO fundamental conflict — mostly reinforces existing pins.** Agreement list:

1. "Edges describe knowledge. Policy describes access" ≈ research pin #4 (no ACLs in graph; enforcement at API layer). W10 deferral already assumes this.
2. "Every agent gets an identity" ≈ W2 pin (agent → Subject later; don't pre-commit to anonymity).
3. Provenance-aware permissions (derived knowledge inherits restrictive provenance of sources) — STRONGEST alignment: Tortoise's points/evidence/EP/authoredBy structure is built for this. Product differentiator, not a conflict.
4. "Don't create a graph for every tiny permission boundary" — validates property-based scoping inside the one org graph over per-subgraph named graphs.
5. Separate authorization graph (Principal/Scope/Capability; security principals as scopes ≈ org/team/role Subjects) connected by stable IDs — consistent with storage=scoped, enforcement=API-layer.
6. Rejects permission-by-topology (semantic edges never grant access) — consistent with uniform actor model + set-once fork.

**Two refinements adopted (note the wording):**
- **R1 (adopted into pin #3):** provenance on EVERY node; security scope on GOVERNING CONTAINERS (inheritance down) with node-level overrides only for exceptions. Not scope-on-every-node.
- **R2 (recorded for W10/RBAC):** deny-wins + default-inheritance + explicit-grants is the leading candidate policy model; pin #5 left additive-vs-deny open — this is the recommendation to adopt at the RBAC phase.

**One genuine tension (future phase, NOT an epic conflict):**
- FalkorDB named graphs for COARSE isolation (company_graph / team_graph / agent_graph_001) vs property-based scoping in the one org graph. Named graphs sever cross-subgraph edges + EP propagation → one-graph-per-org stays correct for this epic; named graphs only if true physical isolation needed later (e.g. agent-private sandbox where cross-edges are semantically forbidden anyway). Record as a conscious W10 decision.

**Conceptual payoff (answers "different subgraphs might have different axes"):**
- `EffectiveGraph(agent) = CanonicalGraph ∩ ReadPolicy` — subgraph axes are LOGICAL VIEWS over one canonical graph, not physical partitions. Four orthogonal capability axes: READ / TRAVERSE / COMPUTE / WRITE. Same underlying fact, different agents compute over entitled subsets (incl. different confidences — "permission-aware epistemic computation"). Aligns with the epic's one-graph-per-org decision and the actor model.

## What this means for the epic (scope guard)

- **In scope (this epic):** nothing new. The three pins from the earlier team discussion (fork orthogonality; team-intent question at org-create; no fine-grained authz / no agent special-casing / no pre-committing agents to anonymity) + the W2 research line.
- **Deferred (separate exploration):** permission management (per-actor scopes), subgraph access axes, per-subgraph retention — all future; the design now demonstrably does not foreclose them.

## Sources

Notion Help (permissions, teamspaces); Confluence DC docs (space permissions, restrictions); Obsidian (vault security); Roam docs; Mem docs (mem.ai global-graph spaces vs mem.v isolated-graph spaces); Fabric Workspaces API; Dust docs + "Permissions in the age of AI-driven companies" (2025); Mem0 docs + "AI agent memory governance" / "Multi-agent memory systems"; Letta docs (shared memory blocks) + Letta Code cross-agent guard PR #1852; Claude Code/Cursor memory scopes; Zep (provenance, group_id); Graphiti (bi-temporal); MemFabric (scope/scope_id); Rezazadeh et al. Collaborative Memory (arXiv:2505.18279); Neo4j (fine-grained access control, property-based ACL overhead, no cross-db relationships); FalkorDB (GRAPH.QUERY/LIST, multigraph multi-tenancy); Rewind/Recall; OpenFGA, Cerbos, WorkOS fine-grained permissions.
