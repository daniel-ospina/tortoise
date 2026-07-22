# Tortoise — Semantic + Epistemic + Episodic + Procedural Graph Engine

Multi-ontology graph engine for agent memory. FalkorDB-backed, multi-tenant, with connector system (GitHub, Linear, Slack), and belief propagation via Expectation Propagation (EP).

**What Tortoise does:**
- Extracts claims (Points) from documents and conversations
- Models belief relationships (IMPL/NAND) with shock propagation
- Tracks provenance chains (Point → Source → Entity) across connectors
- Governs entity ownership (spin-off, access control, audit)
- Exposes graph operations via SDK, MCP server, and agent tools
- Multi-tenant: 10K+ isolated graphs via `graph_name`

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
pip install -e .
python -m tortoise rebuild
```

## License

AGPLv3 + CLA (Apache 2.0 re-license available)
