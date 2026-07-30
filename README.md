# Tortoise

**Semantic + Epistemic + Episodic + Procedural Graph Engine**

A product of [Premise Labs](https://premiselabs.co).

Tortoise is a multi-ontology graph engine for agent memory. FalkorDB-backed, multi-tenant, with a connector system (GitHub, Linear, Slack) and belief propagation via Expectation Propagation (EP).

## What Tortoise Does

- Extracts claims (Points) from documents and conversations
- Models belief relationships (IMPL/NAND) with shock propagation
- Tracks provenance chains (Point → Source → Entity) across connectors
- Ingests external data (GitHub, Linear, Slack) as Events
- Governs entity ownership (spin-off, access control, audit)
- Exposes graph operations via SDK, MCP server, and agent tools
- Multi-tenant: 10K+ isolated graphs via `graph_name`

## Four Ontologies in One Graph

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

## Quick Start

```bash
# 1. Set up environment
cp .env.example .env
# Edit .env with your FalkorDB credentials

# 2. Start FalkorDB (Docker required)
docker compose up -d

# 3. Install
pip install -e .

# 4. Initialize
export $(cat .env | xargs)
python -m tortoise init
```

## Documentation

- [Architecture Index](index.md) — Architecture, API, connectors, and operations
- [Skills Guide](skills/how-to-use-tortoise/SKILL.md) — Agent skill reference for graph operations

## License

Business Source License 1.1 — see [LICENSE](LICENSE)

## About Premise Labs

[Premise Labs](https://premiselabs.co) is an AI lab building the premises intelligence stands on. Tortoise is our flagship product.
