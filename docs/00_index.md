---
title: "Documentation Index"
type: index
domain: operations
doc_status: live
created: 2026-08-08
ownedBy: epistemic-team
---

# Documentation Index

| Area | Path |
| --- | --- |
| Ask answer surface (`POST /v1/ask` / SDK `ask()` / MCP `tortoise_ask`, #1987) | `docs/product/answer-surface.md` |
| Ask pre-ship gate runbook + #2069 routing/cost record (#1987/#2069) | `docs/runbook/1987-ask-abstention-check.md` |
| Auth architecture — standard patterns vs Tortoise (#1498/#1506); key-permission model + #2082 boundary (§6, epic #2083) | `docs/auth-architecture.md` |
| Backup/DR runbook | `docs/ops/registry-backup-dr.md` |
| CI timing measurement artifact (#1477) | `docs/ci-timing.md` |
| CI audit — measured runner-cost/lane analysis, read-only (2026-09-02) | `docs/research/2026-09-02-ci-audit.md` |
| Post-flip verification runbook (#669) | `docs/ops/669-post-flip-verification.md` |
| Ontology | `docs/ONTOLOGY.md` |
| Registry graph schema (incl. Graph entity + scoped APIKey + quota — epic #2083) | `docs/registry-graph-schema.md` |
| Definitions — account layer vs in-graph Subjects ("organization account" vocab note, #2311) | `docs/registry-graph-schema.md` (§ Definitions) · `docs/ONTOLOGY.md` §5/§6 |
| Multi-graph migration runbook — no-forced-migration path + rollback drill (epic #2083, C8 #2117) | `docs/ops/multi-graph-migration-runbook.md` |
| Retrieval latency benchmark runbook (#316) | `benchmarks/README.md` |
| Memory-system comparison table + publication/errata discipline (epic #2080 W7) | `docs/benchmarks/comparison-systems.md` |
| Agent-harness landscape — volunteering-memory end-state distribution targets (epic #2080) | `docs/research/2026-09-01-gbrain-learnings/platform-landscape.md` |
| Embedder selection decision record (ADR-009, #1349) | `docs/adr/ADR-009-embedder-selection.md` |
| Auth planes — sessions vs machine credentials (ADR-010, #2246) | `docs/adr/ADR-010-auth-planes-session-agent-key.md` |
| #2246 dashboard session-only scope + verified plan (ADR-010) | `docs/scoping/2026-09-04-2246-dashboard-session-only.md` |
| #2514 planted-operator (layer-2) corpus + grading scoping | `docs/scoping/2026-09-07-2514-operator-corpus.md` |
| Hosted-vs-local embedding UX research (#1349) | `docs/research/2026-08-17-1349-embedder-selection/ux-research.md` |
| E2E-8 latency re-validation scoping/research (#1656) | `docs/scoping/2026-08-24-1656-e2e8-latency-revalidation-scoping.md` |
| Retrieval levers research (#1657) | `docs/research/2026-08-24-1657-retrieval-levers/research.md` |
| Temporal reasoning in competitor agent-memory systems (2026-09-02) | `docs/research/2026-09-02-temporal-reasoning-competitors.md` |
| Kinds classification-later deep research (#1695) | `docs/research/2026-08-26-classification-later.md` |
| Reaper race scoping/research (#1658) | `docs/scoping/2026-08-24-1658-reaper-race-scoping.md` |
| Test (b) lane 403/export-delete scoping (#2090) | `docs/scoping/2026-09-01-2090-test-b-lane-scoping.md` |
| Test B-wave shared-fixture scoping (#2127) | `docs/scoping/2026-09-02-2127-b-waves-scoping.md` |
| Ingest contract (`tortoise_ingest` / `sdk.ingest` bundle API) | `docs/INGEST_CONTRACT.md` |
| Data safety — encryption in transit + at rest | `docs/data-safety.md` |
| Quickstart — hosted | `docs/quickstart-cloud.md` |
| Quickstart — self-hosted | `docs/quickstart-selfhosted.md` |
| Client/server package split (`tortoise-client` thin driver, #526) | `docs/client-server-split.md` |
| Beta feedback & bug reporting (#1199) | `docs/beta-feedback.md` |
| Blog keyword research — topic taxonomy + keyword map (#1862) | `docs/research/2026-08-28-tortoise-blog-keywords/research.md` |
| Tortoise agent daemon epic research — OAuth/attribution second-pass verdict + industry code audit (#2554, 2026-09-08) | `docs/research/2026-09-08-tortoise-agent-daemon.md` |
| Actor attribution in agent-memory graphs — industry code audit (Zep/Mem0/Letta/LangMem/basic-memory, #2554) | `docs/research/memory-attribution-industry-findings.md` |
| Standard-harness reuse audit — do we need to invent the battery's key decisions (#1416, 2026-09-10) | `docs/research/2026-09-10-standard-harness-reuse-audit.md` |
| Epic #2554 scoping (REVISED) — keyless agent connect via per-agent OAuth + human attribution on the graph | `docs/scoping/tortoise-agent-daemon.md` |
| #2600 Attribution Phase 1 scoping — resolver human actor; sessions + write-events stamped (epic #2554 v1 gate) | `docs/scoping-2600-attribution-phase1.md` |
| #2789 one free organization per person — scoping (double diamond, Design 2 = webhook-provisioned org) | `docs/scoping/2026-09-10-2789-one-free-org.md` |
| #2789 one free organization per person — implementation plan | `docs/plans/2026-09-10-2789-one-free-org.md` |
