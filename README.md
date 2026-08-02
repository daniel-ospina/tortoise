# Tortoise — Semantic + Episodic + Epistemic + Procedural Graph Engine

A product of [Premise Labs](https://premiselabs.co). Tortoise is a multi-ontology graph engine for agent memory. FalkorDB-backed, multi-tenant, with expansion packs, hybrid search, and belief propagation via Expectation Propagation (EP).

## Four Ontologies in One Graph

Tortoise models reality in four interconnected layers. The **semantic** layer is the foundation — it captures *what exists*. The **episodic** and **epistemic** layers provide context around it — *what happened* and *why*. Together they let the semantic layer evolve as new evidence arrives and events unfold.

### Semantic — What exists

The base layer. Teams, features, documents, products, customers, code, agreements — anything that *is*. This is the ground truth that episodic and epistemic layers reference.

| Concept | Examples |
|---------|---------|
| Entities | Subject, Object, Document, Feature, Team, Agreement |
| State | `active`, `deprecated`, `superseded`, `in_progress` |
| Relationships | `owns`, `dependsOn`, `partOf`, `assignedTo` |

When something changes — a feature is shipped, a team is reorganized, a contract is signed — the semantic layer updates. **Confidence flows from epistemic evidence; state changes from episodic events.**

### Episodic — What happened

Events that occur over time. Agent conversations, meetings, call recordings, GitHub commits, Slack threads, CI/CD runs — all ingested as timestamped Events. Every event connects to the semantic entities it involves.

| Concept | Examples |
|---------|---------|
| Events | Meeting, Conversation, Deployment, Review, Extraction |
| Connections | Event `involves` Feature, Agent `participatedIn` Meeting |
| Ingestion | GitHub webhooks, Linear sync, Slack connectors, agent session capture |

> **Current pipelines:** Agent conversations → tortoise-capture → extraction → Points. Meetings → transcript → extraction. Call recordings → transcript → extraction. All flow into the episodic layer and link to semantic entities.

### Epistemic — What we believe

Claims, arguments, decisions — *why* we think something is true. Every Point has a confidence score. IMPL edges say "this supports that." NAND edges say "this contradicts that." EP propagation updates belief across the graph when new evidence arrives.

| Concept | Examples |
|---------|---------|
| Claims | Point (statement, decision, observation, hypothesis) |
| Support | IMPL — "A provides positive epistemic support for B" |
| Contradiction | NAND — "A logically contradicts B" |
| Confidence | Beta distribution (α, β) — updated via EP propagation |
| Evolution | Supersession, deprecation, confidence drift over time |

When new evidence arrives (an experiment result, a user interview, a code review), EP propagates confidence through the graph. High-confidence claims survive. Contradicted claims weaken. Superseded claims are replaced.

### Procedural — How work flows

Actions, workflows, tools — *how* things get done. Connects agents to their work and skills to their outputs.

| Concept | Examples |
|---------|---------|
| Actions | Research, Scope, Plan, Implement, Verify, Reflect |
| Connections | Agent `performs` Action, Action `produces` Point |

### How the layers connect

```
┌──────────────────────────────────────┐
│              SEMANTIC                │
│         (what exists)                │
│  Feature, Team, Document, Agreement  │
│                                      │
│  ┌──────────┐      ┌──────────┐      │
│  │ EPISODIC │      │EPISTEMIC │      │
│  │(when)    │      │(why)     │      │
│  │Meeting   │      │Argument  │      │
│  │Commit    │      │Decision  │      │
│  │Call      │      │Evidence  │      │
│  └────┬─────┘      └────┬─────┘      │
│       │                 │            │
│       └────────┬────────┘            │
│                │                     │
│   Context flows into the semantic    │
│   layer. Confidence changes state.   │
│   Events trigger updates.            │
│                                      │
│            PROCEDURAL                │
│         (how it happens)             │
│   Action, Workflow, Tool, Skill      │
└──────────────────────────────────────┘
```

A Feature exists (semantic). A meeting discussed it (episodic). An argument was made that it should be deprioritized (epistemic). EP propagation reduces confidence. The feature's state changes to `deprecated`. All connected.

## What Tortoise Does

- **Indexes everything.** Semantic entities, episodic events, epistemic claims — all connected in one FalkorDB graph.
- **Propagates belief.** EP engine updates confidence across the graph when new evidence arrives.
- **Connects the layers.** A decision in a meeting (episodic) connects to the argument that supported it (epistemic), which connects to the feature it's about (semantic).
- **Evolves state.** Confidence changes → semantic entities get superseded, deprecated, or strengthened.
- **Ingests from everywhere.** GitHub, Linear, Slack, agent sessions, meeting transcripts, call recordings.
- **Expands with packs.** Domain-specific ontologies (project management, product strategy, marketing) via expansion packs.
- **Searches across layers.** Hybrid search (full-text + vector + structural) with RRF fusion.
- **Exposes via SDK + MCP.** Agents query, create, and traverse the graph through Python SDK and MCP tools.

## Domain-Specific Business Logic (Expansion Packs)

Expansion packs aren't just more kinds — they embed domain-specific business logic into the graph. Each pack pre-defines how its concepts relate, following established frameworks and best practices for that domain.

### Example: Product Strategy Pack

```
Customer Segments (who we serve)
        │
   Job To Be Done (what they need)
        │
   Features (what we build)
        │
   User Journeys (how they use it)
        │
   Workflows (step-by-step flows)
        │
   Requirements (what enables them)
```

The relationships are pre-mandated — not arbitrary. A feature doesn't just "exist" in the graph; it connects upstream to the Jobs To Be Done it satisfies and the Customer Segments it serves, and downstream to the User Journeys, Workflows, and Requirements that implement it. Confidence about market needs flows through EP propagation to confidence about features. Evidence from user research strengthens Jobs To Be Done. Deployment events trigger state changes in Requirements.

This is fundamentally different from a generic semantic graph that just says "here is some data with labels." The expansion pack encodes the relationship logic that comes from product management methodology — so the graph can explain not just *what* exists, but *why* it exists and *how* it connects.

The same principle applies to every pack:
- **Project Management**: Issues → Sprints → Milestones, with estimation and retrospective loops
- **Marketing**: Campaigns → Audiences → Assets → Ad Creatives, with targeting and measurement hooks
- **CRM**: Contacts → Deals → Companies, with pipeline stages and activity tracking

Expansion packs are free at all pricing tiers. You can create your own. They make the graph usable in the language of your domain, not just generic types.

## Pricing

| Tier | Price | Features |
|------|-------|----------|
| **Free (Community)** | $0 | Full CRUD, single-user, single-instance. Core ontology + expansion packs. Community support. |
| **Pro (Team)** | $20–50/user/mo | Multi-user, team workspaces, basic RBAC, increased API limits, priority support |
| **Enterprise** | Contact | SSO/SAML, SCIM, audit logs, custom governance, air-gapped deployment, 99.9% SLA, dedicated support |

Custom ontology packs are **free at all tiers**. Governance features (kind lifecycle, schema versioning) are Team+. See pricing research (internal).

## Architecture

```
Data Pipelines (GitHub, Linear, Slack, agent sessions, call recordings)
        ↓
JSONL Event Log (append-only source of truth)
        ↓
Projection (rebuildable current state)
        ↓
FalkorDB Graph (semantic + episodic + epistemic + procedural)
        ↓
SDK (Python API) + Pack Registry (expansion packs)
        ↓
MCP Server (agent tools) + CLI (tortoise pack, tortoise query)
        ↓
Hybrid Search (FTS + vector + RRF fusion)
```

## Quick Start

```bash
# Start FalkorDB (Docker required)
docker run -d -p 6379:6379 --name falkordb falkordb/falkordb:latest

pip install tortoise
tortoise init                 # creates DB, installs starter expansion packs
tortoise pack list            # see loaded packs and available kinds
tortoise connect github       # connect your repos
tortoise serve --dashboard    # start MCP server + web dashboard
```

## Documentation

- [Architecture Index](index.md) — Architecture, API, connectors, and operations
- [Skills Guide](skills/how-to-use-tortoise/SKILL.md) — Agent skill reference for graph operations
- Expansion Pack Architecture (Epic #7618) — Domain-specific ontology packs
- Hybrid Search Epic (#7697) — FTS + vector + RRF retrieval

## License

Business Source License 1.1 — see [LICENSE](LICENSE)

## About Premise Labs

[Premise Labs](https://premiselabs.co) is an AI lab building the premises intelligence stands on. Tortoise is our flagship product.
