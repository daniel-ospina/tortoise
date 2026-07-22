---
title: "Tortoise — Canonical Index"
type: index
domain: data
status: live
created: 2026-07-09
updated: 2026-07-21
---

# Tortoise — Canonical Documents

## Architecture
- [Projection layer](tortoise/projection/) — JSONL → FalkorDB projection, grounding, belief propagation
- [SDK](tortoise/sdk.py) — Python API for entity CRUD, edge creation, query helpers
- [MCP Server](tortoise/mcp_server.py) — Agent-facing tools via FastMCP
- [Connectors](tortoise/connectors/) — GitHub, Linear, Slack data ingestion
- [Extractor](tortoise/extractor.py) — Semantic extraction from documents/transcripts

## Data
- [ONTOLOGY v2.5](https://github.com/daniel-ospina/eldato/blob/main/docs/teams/organisation-design-team/domains%20(S1)/data/ONTOLOGY_v2.5.md) — Canonical entity & edge spec (external, in eldato/docs)
- [Embedding & Retrieval](data/embedding-retrieval.md) — 3-tier model + query patterns
- [Memory Types Taxonomy](data/MEMORY_TYPES.md) — Canonical 5-type taxonomy

## Operations
- [Backup & Restore](tortoise/backup.py)
- [Migration Guide](scripts/) — Backfill, schema migration
- [Multi-Tenancy](tortoise/projection/__init__.py) — graph_name isolation

## Product
- [V1 Strategy](product/strategy/v1-strategy-2026-07-09.md)
- [Competition](product/competition/_index.md)

## Testing
- [Test suite](tests/) — 950 tests, 788 passing
- [Deprecated SVBP tests](tests/deprecated_svbp/) — Replaced by EP (ep.py)

## License
AGPLv3 + CLA — see [LICENSE](LICENSE)

## Related Repositories
- [eldato](https://github.com/daniel-ospina/eldato) — Main El Dato app + canonical ONTOLOGY v2.5
- [eldato-outreach](https://github.com/daniel-ospina/eldato-outreach) — B2B WhatsApp outreach system
- [dmer](https://github.com/daniel-ospina/dmer) — Instagram DM automation daemon
- [org-data](https://github.com/daniel-ospina/org-data) — Multi-tenant org data (Supabase → Tortoise connector)
