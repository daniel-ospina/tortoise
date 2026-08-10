---
title: "Tortoise — Canonical Index"
type: index
domain: data
status: live
created: 2026-07-09
updated: 2026-08-10
---

# Tortoise — Canonical Documents

## Architecture
- [MCP Server](tortoise/mcp_server.py) — Agent-facing tools via FastMCP (primary interface; 58 tools)
- [Self-Host Daemon](tortoise/selfhost.py) — thin single-tenant service: MCP Streamable HTTP at /mcp + /health
- [Connectors](tortoise/connectors/) — GitHub, Linear, Slack data ingestion
- [MCP Client](tortoise/mcp_client.py) — thin driver for scripts/integrations (connect, don't import)
- [SDK](tortoise/sdk.py) — Python API for local dev/scripting (connects to a daemon)
- [Projection layer](tortoise/projection/) — JSONL → FalkorDB projection, grounding, belief propagation
- [Extractor](tortoise/extractor.py) — Semantic extraction from documents/transcripts

## Data
- [ONTOLOGY v3.4](docs/ONTOLOGY.md) — Canonical entity & edge spec (co-located with the code it governs)
- [Embedding & Retrieval](data/embedding-retrieval.md) — 3-tier model + query patterns
- [Memory Types Taxonomy](data/MEMORY_TYPES.md) — Canonical 5-type taxonomy

## Operations
- [Backup & Restore](tortoise/backup.py)
- [Graph Scripts](graph-scripts/) — Historical graph operations (pricing, migrations, audit)
- [Multi-Tenancy](tortoise/projection/__init__.py) — graph_name isolation

## Product
- [V1 Strategy](product/strategy/v1-strategy-2026-07-09.md)
- [Competition](product/competition/_index.md)

## Testing
- [Test suite](tests/) — 950 tests, 788 passing
- [Deprecated SVBP tests](tests/deprecated_svbp/) — Replaced by EP (ep.py)

## License
Business Source License 1.1 (free self-hosted production use under $5,000,000 annual revenue; Mozilla Public License 2.0 conversion after 4 years) — see [LICENSE](LICENSE) and [license notes](docs/license-notes.md)

## Related Repositories
- [eldato](https://github.com/daniel-ospina/eldato) — Main El Dato app
- [eldato-outreach](https://github.com/daniel-ospina/eldato-outreach) — B2B WhatsApp outreach system
- [DMeer](https://github.com/daniel-ospina/DMeer) — Instagram automation daemon with Electron tray app
- [swarm](https://github.com/daniel-ospina/swarm) — Multi-tenant organizational data management (teams, products, roles, features; Supabase-backed, connectors for the Tortoise knowledge graph)
