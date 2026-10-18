---
title: "Reference — Information-Oriented Documentation"
type: index
domain: operations
doc_status: live
created: 2026-10-16
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Reference (Information-Oriented)

> **Diátaxis Quadrant:** Reference documentation provides authoritative, accurate technical descriptions of APIs, schemas, ontologies, limits, and operational metrics.

New reference specifications should be added to `docs/reference/`.

## Ontology, Data Schemas & Contracts

| Specification | Path | Summary |
|---|---|---|
| **Canonical Ontology** | [`docs/ONTOLOGY.md`](../ONTOLOGY.md) | Entity types (`Point`, `Source`, `Subject`, `Object`, `Event`), epistemic operators, and edge predicates. |
| **Registry Graph Schema** | [`docs/registry-graph-schema.md`](../registry-graph-schema.md) | Account layer vs. in-graph Subjects, `Graph` entity, scoped `APIKey`, and quota schema. |
| **Ingest Contract** | [`docs/INGEST_CONTRACT.md`](../INGEST_CONTRACT.md) | Bundle API contract for `tortoise_ingest` and `sdk.ingest`. |
| **Capacity & Limits** | [`docs/capacity.md`](../capacity.md) | Write concurrency by deployment mode, rate limits, `max_graph_nodes` plan caps, latency targets, and storage footprint. |
| **Webhook Consumer Contract** | [`docs/reference/webhook-consumer-contract.md`](webhook-consumer-contract.md) | The receiving half of the #2081 push path: exact headers (`X-Tortoise-Signature`, `X-Tortoise-Delivery`, `X-Tortoise-Event`, `X-Tortoise-Seq`), the delivery envelope, signature verification, at-least-once dedupe rules, retry/backoff + disable semantics, receipt reads, and the gaps measured by the pilot (#8102). |

## MCP & Public SDK Surface Manifests

| Surface Artifact | Path | Summary |
|---|---|---|
| **Curated MCP + SDK Surface** | [`docs/product/mcp-sdk-surface.md`](../product/mcp-sdk-surface.md) | Rendered from `config/surface-manifest.yml`: every served MCP tool, public SDK method, and recorded `response_fields` entry. |
| **Canonical MCP Tool List** | [`docs/product/canonical-mcp-tools.md`](../product/canonical-mcp-tools.md) | Owner-approved target MCP surface, read/write separation, and approval metadata. |
| **Beta SDK Surface** | [`docs/product/beta-sdk-surface.md`](../product/beta-sdk-surface.md) | Target SDK memory and multi-tenant graph management surface. |
| **Canonical SDK Method Inventory** | [`docs/product/canonical-sdk-methods.md`](../product/canonical-sdk-methods.md) | Full inventory of all 150 public `TortoiseSDK` methods across 32 groups. |
| **Declared SDK Public Surface** | [`docs/product/sdk-surface-declaration.md`](../product/sdk-surface-declaration.md) | AST-derived public method set reconciled against runtime reflection (`tools/sdk_surface.py`). |
| **Bridge Table** | [`docs/product/bridge-table.md`](../product/bridge-table.md) | Generated mapping of current tools to target destinations (`tools/bridge_table.py`). |
| **MCP Rename Table** | [`docs/product/mcp-rename-table.md`](../product/mcp-rename-table.md) | Caller migration table for retired and renamed MCP tools (`tools/mcp_rename_table.py`). |
| **Ask Lane Surface (Eval-Only)** | [`docs/product/answer-surface.md`](../product/answer-surface.md) | Specification for `tortoise/ask_lane.py` (eval-only; no public REST/SDK/MCP exposure). |

## Cost, Storage & Benchmark Reference

| Metric / Comparison Spec | Path | Summary |
|---|---|---|
| **Fixed / Shared SaaS Cost Allocation** | [`docs/ops/cost-allocation.md`](../ops/cost-allocation.md) | Mechanical per-line cost allocation rule and `tortoise_team_cost_cents` metric definition. |
| **Postgres Storage Footprint & Row Growth** | [`docs/ops/postgres-storage-measurement.md`](../ops/postgres-storage-measurement.md) | Read-only SQL measurement functions (`postgres_storage_footprint()`, `analytics_events_rows_by_org()`). |
| **Extraction Cost Metric Set** | [`docs/ops/extraction-cost-metrics.md`](../ops/extraction-cost-metrics.md) | Per-call token/model/stage metrics and disclosure counters. |
| **Memory-System Comparison Table** | [`docs/benchmarks/comparison-systems.md`](../benchmarks/comparison-systems.md) | Comparison matrix and publication/errata discipline across agent memory systems. |
