---
title: "Tortoise — Canonical Index"
type: index
domain: data
status: live
created: 2026-07-09
updated: 2026-07-30
---

# Tortoise — Documentation Index

## Architecture
- [Projection layer](tortoise/projection/) — JSONL → FalkorDB projection, grounding, belief propagation
- [SDK](tortoise/sdk.py) — Python API for entity CRUD, edge creation, query helpers
- [MCP Server](tortoise/mcp_server.py) — Agent-facing tools via FastMCP
- [Connectors](tortoise/connectors/) — GitHub, Linear, Slack data ingestion
- [Extractor](tortoise/extractor.py) — Semantic extraction from documents/transcripts

## Operations
- [Backup & Restore](tortoise/backup.py)
- [Multi-Tenancy](tortoise/projection/__init__.py) — graph_name isolation

## Agent Skills
- [How to Use Tortoise](skills/how-to-use-tortoise/SKILL.md) — Agent skill reference for graph operations
- [Tortoise Audit](skills/tortoise-audit/SKILL.md) — Graph structure audit
- [Tortoise Decide](skills/tortoise-decide/SKILL.md) — Decision engine with graph reasoning
- [File Finding](skills/tortoise-file-finding/SKILL.md) — Ingest research findings
- [File JTBD](skills/tortoise-file-jtbd/SKILL.md) — Create jobs-to-be-done with use cases
- [Verify Chain](skills/tortoise-verify-chain/SKILL.md) — Chain integrity verification

## Testing
- [Test suite](tests/) — EP belief propagation, SDK, connectors, projections

## License
Business Source License 1.1 — see [LICENSE](LICENSE)

## About
Tortoise is a product of [Premise Labs](https://premiselabs.co).
