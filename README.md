---
title: "Tortoise — Semantic + Epistemic + Episodic + Procedural Graph Engine"
type: readme
domain: 
status: seedling
tags: []
summary: ""
created: 2026-07-24
updated: 2026-07-24
---

# Tortoise — Semantic + Epistemic + Episodic + Procedural Graph Engine

Multi-ontology graph engine for agent memory. FalkorDB-backed, multi-tenant, with connector system (GitHub, Linear, Slack), and belief propagation via Expectation Propagation (EP).

**What Tortoise does:**
- Extracts claims (Points) from documents and conversations
- Models belief relationships (IMPL/NAND) with shock propagation
- Tracks provenance chains (Point → Source → Entity) across connectors
- Ingests external data (GitHub, Linear, Slack) as Events per ONTOLOGY_v2.5
- Governs entity ownership (spin-off, access control, audit)
- Exposes graph operations via SDK, MCP server, and agent tools
- Multi-tenant: 10K+ isolated graphs via `graph_name`

**What lives elsewhere:**
- Coordination infrastructure (cards, Kanban boards, agent workflows, dashboards)
  lives in `eldato/operations/coordination/` — owned by the Organisation Design Team.
- Canonical ontology (`ONTOLOGY_v2.5.md`) lives in `eldato/docs/teams/`.
- Tortoise is the memory engine — it stores and queries knowledge. It does not
  coordinate agents.

**Four ontologies in one graph:**
| Layer | What it models | Examples |
|-------|---------------|---------|
| Semantic | What exists | Subject, Object, Document |
| Epistemic | What we believe | Point, IMPL, NAND, confidence |
| Episodic | What happened | Event, instantiates, participatesIn |
| Procedural | How work flows | Action, performs, produces, dependsOn |

## Architecture

```
Connectors (GitHub/Linear/Slack)
  → JSONL Event Log (append-only source of truth)
    → Projection (rebuildable current state)
      → FalkorDB Graph
        → SDK (Python API)
          → MCP Server (agent tools)
```

## Ontology

Canonical entity model: [ONTOLOGY v2.5](https://github.com/daniel-ospina/eldato/blob/main/docs/teams/organisation-design-team/domains%20(S1)/data/ONTOLOGY_v2.5.md)
— 7 entity types, 22 edge types, full PROV-O/DC/schema.org alignment.

## Documentation

See [index.md](index.md) for architecture, API, connectors, and operations.

## Quick Start

```bash
# 1. Set up environment
cp .env.example .env
# Edit .env if your FalkorDB password/host differs

# 2. Start FalkorDB (Docker required)
docker compose -f ../eldato/operations/memory/docker-compose.yml up -d

# 3. Install and initialize
pip install -e .
export $(cat .env | xargs)  # load env vars
python -m tortoise init     # auto-detects Docker, creates welcome Point
```

See [.env.example](.env.example) for all available environment variables.

## License

AGPLv3 + CLA (Apache 2.0 re-license available)

## Related Repositories
- [eldato](https://github.com/daniel-ospina/eldato) — Main app + canonical [ONTOLOGY v2.5](https://github.com/daniel-ospina/eldato/blob/main/docs/teams/organisation-design-team/domains%20(S1)/data/ONTOLOGY_v2.5.md)
- [eldato-outreach](https://github.com/daniel-ospina/eldato-outreach) — B2B WhatsApp outreach
- [dmer](https://github.com/daniel-ospina/dmer) — Instagram DM daemon
- [org-data](https://github.com/daniel-ospina/org-data) — Org data (Supabase → Tortoise)
- [premiselabs.co](https://premiselabs.co) — Landing page ([source](premise-labs/index.html))
