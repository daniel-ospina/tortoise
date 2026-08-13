---
title: "Research Brief — Issue #405: Domain Integrity Constraint System"
type: synthesis
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

# Research Brief — Issue #405: Domain Integrity Constraint System

> **Issue:** daniel-ospina/tortoise#405 (re-scoped 2026-08-13, tier standard)
> **Epic:** docs/epics/2026-07-14-memory-system/04-plan.md (homed in eldato — #6886 origin)
> **Persist note:** `scripts/_research_append.sh` could not resolve a brief path (epic not local; no `**Research:**` field) — findings persisted manually per Phase 1.5 Sub-step C with the same timestamped/source-tagged format.

## Axis Research

### Ontology axis (rated HIGH)

- **Canonical — SHACL severity + constraint separation:** W3C SHACL deliberately separates constraint *definition* from *enforcement strategy*; the same shapes can drive transactional enforcement, CI validation, scheduled checks, or compliance reporting (neo4j.com GRAPH TYPE vs RDF/SHACL comparison, 2026-06). SHACL severity model: warnings never block unless a shape is individually marked mandatory; invalid data is a normal, supported state (persist-and-flag).
- **Competitor-precedent — Cognee ontology grounding:** Cognee validates LLM-extracted entities against a supplied OWL/RDF ontology at ingest and stamps each node with an `ontology_valid` flag — "so you can tell grounded entities from hallucinated ones" (github.com/topoteretes/cognee; codepointer.substack.com 2026-06). Validation-as-flag, not validation-as-reject. ⚠️ medium (2 sources, same ecosystem cluster).
- **Competitor-precedent — Mem0 removed graph memory:** Mem0 v3 (Apr 2026) removed its graph layer after its paper showed the graph variant lost on recall, ran 3× slower, cost 2× tokens; replaced with entity linking (hub-and-spoke `linked_memory_ids`). Cautionary tale for graph-rule machinery cost (codepointer.substack.com 2026-06). ⚠️ medium.
- **Pitfalls — drifting validation artifacts:** "The shapes graph is a separate document that can drift from the data it governs if it isn't actively managed… Three languages and a drifting artifact, versus one" (neo4j.com GRAPH TYPE article). "Checks that still assert the old shape either reject valid data or wave through invalid data. Run automated drift detection on a schedule" (graph-data-modeling.org). Directly supports the confirmed problem's three-divergent-chain-definitions root cause.

### Architecture axis (rated HIGH)

- **Canonical — three validation gates:** Production-grade graph integrity embeds validation in three gates — pre-load schema check (fail fast), in-loop in-transaction validation (rollback chunk, route to dead-letter queue), post-load graph-wide reconciliation (alert). Guiding principle: *"the cheapest check that can catch a class of defect should run at the earliest gate that has enough information to run it"* (graph-data-modeling.org). The commit path in this repo lacks graph state → chain checks cannot run there; they belong at the post-load/read side. High confidence (2+ independent documentation sources).
- **Canonical — SHACL validation modes:** Neo4j neosemantics offers three modes — batch (whole graph), node-set (selected portion), transactional (in-transaction trigger with rollback on violation) (neo4j.com/labs/neosemantics). This maps 1:1 to CLI batch + commit-path transactional + per-domain subset. High.
- **Canonical — "a validation report is not a transactional gate":** SHACL defines a validation *function* (run the check, get a conformance report); the decision to reject at commit is a separate enforcement-strategy choice (neo4j.com GRAPH TYPE article).
- **Pitfalls — mammoth transactions:** Large graph transactions block concurrent progress (throughput drops up to 4.7× during a single mammoth in Neo4j); commit-path validation on big writes is expensive (VLDB 2024, Cheng et al.). Supports warn-only in commit path; heavy checks belong on-demand.
- **Pitfalls — incremental update model:** Batch pipelines break on incremental updates — entity resolution must run against the live graph at the boundary, dirty-neighbor queues handle cascade propagation; "the three problems above are not edge cases — they are the production steady state" (dev.to, 2026-08). Supports the cold-start / incremental-capture concern (useCase before JTBD is the normal pattern).
- **Emerging — write-time governance layer with receipts:** CMGL (github.com/kadubon/certified-memory-governance-layer, 2026-05) is an external governance layer between agent runtime and memory backend — admit/block decisions with typed receipts, append-only ledger, fail-closed. Alternative architecture: external gate rather than in-engine constraint. ⚠️ single-source.

### UX / CLI axis (rated LOW, fired on demonstrated gap — exit-code contract)

- **Canonical — CLI exit codes:** `0` = success, `1` = general error, `2` = misuse/bad usage is the universal convention (tessl.io; stackoverflow Linux conventions; python-cli-toolcraft.com; cli-agent-spec.github.io). More granular: `3` = mid-operation failure, `4` = missing precondition (cli-agent-spec). POSIX sysexits (e.g., 64 EX_USAGE) available for standardization. High (5 sources).

### Deferred-to-research queue (clarifying-questions Pass B)

- **D1 migration story (existing violating graphs):** SHACL persist-and-flag + Cognee `ontology_valid` flag → warn/flag pre-existing violations rather than reject; repair is a separate remediation flow (see tortoise-verify-chain fix options). Covered by ontology axis findings.
- **D3 registration API vs manifest-declared constraints:** SHACL separates definition from enforcement; declarative DB constraints are "the hard guarantee the write can never bypass" (graph-data-modeling.org) — manifest-declared chains are the canonical rule source; a registration API is an enforcement-strategy adapter. Covered.
- **D4 write-time vs read-time:** three-gates principle — earliest gate with enough information; commit path lacks graph state. Covered.
- **D5 cold start:** incremental capture order is normal (dev.to; skill's own post-hoc fix loop). Covered.
- **D7 constraint-check cost:** mammoth transactions caution. Covered.
- **D9 Phase B failure UX:** SHACL transactional mode exists but requires graph visibility; CMGL receipts as a failure-surfacing pattern. Covered.
- **D2 (A→B transition), D8 (MCP exposure), D6 (multi-domain composition):** resolved internally — enforcement config flip via `pack_registry.enforcement_for_chain`; tool_registry auto-derives MCP+REST from ToolDefinition; commit payload multi-domain routing needs a domain field (flagged as precondition).

## Raw Notes

- 2026-08-13 [canonical] SHACL separates constraint definition from enforcement strategy; three validation modes (batch/node-set/transactional); severity model persist-and-flag; validation report ≠ transactional gate. Sources: neo4j.com/labs/neosemantics, neo4j.com GRAPH TYPE article (2026-06).
- 2026-08-13 [canonical] Three validation gates (pre-load / in-transaction / post-load reconciliation); "cheapest check at earliest gate with enough information"; declarative constraints = hard guarantee; drift detection on schedule. Source: graph-data-modeling.org.
- 2026-08-13 [competitor-precedent] Cognee stamps nodes with ontology_valid flag after validating LLM-extracted entities against supplied ontology. Source: github.com/topoteretes/cognee; codepointer.substack.com (2026-06).
- 2026-08-13 [competitor-precedent] Mem0 removed graph memory in v3 (Apr 2026) — graph variant lost recall, 3× slower, 2× tokens; replaced with entity linking. Source: codepointer.substack.com (2026-06).
- 2026-08-13 [competitor-precedent] Graphiti (Zep): bi-temporal fact graph, contradiction → stamp invalid not delete; incremental add_episode ingestion. Source: codepointer.substack.com (2026-06).
- 2026-08-13 [pitfalls] Mammoth graph transactions block concurrency (up to 4.7× throughput drop); commit-path validation on large writes is expensive. Source: VLDB 2024 (Cheng et al.).
- 2026-08-13 [pitfalls] Incremental graph updates break batch pipelines — live-graph entity resolution at boundary, dirty-neighbor queues; cold-start/incremental capture is the production steady state. Source: dev.to (2026-08).
- 2026-08-13 [pitfalls] Structural vs semantic validation boundary (CYGNET): structural gates catch 100% of constraint violations at zero false-positive rate; semantic validation must take over where structure ends. Source: arxiv.org/html/2606.04645.
- 2026-08-13 [canonical] CLI exit codes: 0=success, 1=general error, 2=usage error; optional 3/4 for finer granularity; sysexits available. Sources: tessl.io, python-cli-toolcraft.com, cli-agent-spec.github.io, stackoverflow.
- 2026-08-13 [emerging] CMGL: external governance layer with typed receipts + append-only ledger + fail-closed gates (alternative architecture to in-engine constraints). Source: github.com/kadubon/certified-memory-governance-layer. ⚠️ single-source.
