---
title: "Axis Research — #388 Source nodes for connectors (2026-08-13, Fast intent, 6 post-dedup queries, Exa — Perplexity 429)"
type: synthesis
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

## Axis Research — #388 Source nodes for connectors (2026-08-13, Fast intent, 6 post-dedup queries, Exa — Perplexity 429)

### Ontology axis (medium) — provenance modeling
- canonical: W3C PROV-O/PROV-DM — provenance chains via prov:wasDerivedFrom / prov:hadPrimarySource; primary-source relation: "secondary materials reference their primary sources... so that their reliability can be investigated" — directly mirrors Tortoise (Point)-[:extractedFrom]->(Source)-[:references]->(Entity) layered provenance. PROV-DM component 5: properties linking entities that refer to the same thing (identity/alternate) — relevant to the two-producer Object identity collision (github-issue-{repo}-{number} vs issue_{sha8}). source: https://www.w3.org/TR/prov-o/
- pitfalls: Springer Datenbank-Spektrum 2024 KG-provenance survey — granularity tradeoff: coarse-grained provenance "does not have the granularity to track how each entry of the input dataset was transformed"; finer granularity increases graph complexity considerably. Implication: repo-level Source nodes (github:{repo}) carry near-zero per-entity provenance information; per-entity Source granularity is what the P4 consumer needs. source: https://link.springer.com/article/10.1007/s13222-023-00463-0

### Architecture axis (medium) — where derived provenance is materialized
- canonical: Azure event-sourcing pattern + OpenCQRS + eventsourcing python lib — read models/materialized views are updated by event handlers at ingestion; producers (connectors) are dumb emitters, the projection layer owns graph shape; read models are disposable derived data (rebuild by replay). Validates projection-layer choke point (proj.apply → _upsert_*) as the wiring home, NOT per-connector calls. sources: https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing ; https://docs.opencqrs.com/blog/one-truth-many-views/
- competitor-precedent: metronix-memory GitHub connector PR — per-entity STABLE source_id schemes (gh-issue-*, gh-pr-*, gh-doc-*, gh-release-*) with source_type=github; incremental sync via `since` filter; content-hash dedup. Review warning: `source_role` change silently demoted GitHub evidence for existing installs with "no migration, no test covering the interaction" — direct analog of #388's "0 regressions" indicator blindness (new Source nodes change list_sources/source_stats/provenance behavior silently). Also metadata type-convention break feeding type_counts/source_balance scoring. source: https://github.com/mtrnix/metronix-memory/pull/273
- competitor-precedent: curiosity-ai GitHubSample — source's own primary keys as node keys (GraphQL global id for Issue/PR/Review, nameWithOwner for repos). ucp-gen — every claim cites sources, sources carry content hash, hallucinated citations dropped. sources: https://github.com/curiosity-ai/connector-recipes/tree/main/GitHubSample ; https://pypi.org/project/ucp-gen/
- pitfalls: Protean — "Design Projection Granularity Around Consumer Needs"; mirror-the-aggregate projections are waste. TypeGraph materializing external event logs — projectors MUST be idempotent (at-least-once delivery converges on same state), use stable source ids (upsertById, getOrCreateByEndpoints), coalesce unchanged upserts to suppress churn (directly relevant to _upsert_source version bump on every MATCH). sources: https://docs.proteanhq.com/patterns/projection-granularity/ ; https://typegraph.dev/materializing-event-logs/

### Graph upsert idempotency (architecture pitfalls, cross-cutting)
- Neo4j MERGE semantics — single-node MERGE without unique constraint can duplicate under concurrency; long-pattern MERGEs create duplicates (break into smaller MERGEs); use explicit ON CREATE/ON MATCH. source: https://neo4j.com/developer/kb/understanding-how-merge-works/
- Curiosity idempotency rules — stable deterministic keys from real source identifiers; only immutable fields in derived hashes; deliberate AddOrUpdate vs TryAdd; idempotency test = run connector twice, node counts unchanged. investigraph — include entity type in ID (distinct ID spaces) to avoid collisions (relevant to github-issue- vs issue_{sha8} collision). Koza — non-destructive dedup with provenance columns. sources: https://docs.curiosity.ai/data-connector/idempotency ; https://docs.investigraph.dev/how-to/keys/ ; https://koza.monarchinitiative.org/graph-operations/explanation/data-integrity/
- Backward compat: adding node/edge types is a SAFE backwards-compatible schema change (TypeGraph), but queries that traverse all vertices/edges may behave differently (TigerGraph); existing nodes get empty new edges (Dgraph). Implication: Source nodes + references are additive-safe; consumers listing Sources/provenance must be regression-tested. sources: https://typegraph.dev/schema-evolution/ ; https://www.tigergraph.com/docs/gsql-ref/4.2/ddl-and-loading/modifying-a-graph-schema

### Integration Docs (drafted — no new third-party deps)
- No new deps. All required plumbing is in-repo: SDK create_source (tortoise/sdk.py:6695, MERGE-on-url, dual-write sourceKind→credibilityTier), link_source_to_entity (tortoise/sdk.py:7220, auto-creates Source, labels Document|Event|Object), _upsert_document precedent (tortoise/projection/entities.py:335), SOURCE_KIND_DEFAULTS (tortoise/source_credibility.py — connector kinds neutral/None today).
