#!/usr/bin/env python3
# Historical — uses embedded tortoise.db. Do not run against production Docker.
"""Add convergence architecture research evidence to the Tortoise graph.

Fires 4 architectural questions, maps trade-offs, tags confidence,
connects with IMPL (supports) and NAND (contradicts).
"""
from __future__ import annotations

import sys
sys.path.insert(0, '/Users/home/eldato/negation-game-explorations/tortoise')

from tortoise.log import EventLog
from tortoise.api import EventAPI, provenance
from tortoise.projection import FalkorProjection

LOG_PATH = "/Users/home/eldato/negation-game-explorations/tortoise/convergence-architecture.jsonl"
DB_PATH   = "/Users/home/eldato/negation-game-explorations/tortoise/tortoise.db"

log = EventLog(LOG_PATH)
proj = FalkorProjection()
api = EventAPI(log, initiated_by="user", agent_id="pi@deepseek-v4",
               projection=proj)

SRC = "convergence-architecture-research"
PROV = provenance(SRC, (0, 0), "",
                  extracted_by="pi-web-search@2026-07-14")

ctx = "convergence-architecture"

def P(content):
    """Add a point; return its id."""
    return api.add_point(content, ctx, PROV)

def IMPL(src, tgt, content=None):
    api.add_operator("IMPL", [src, tgt], ctx, PROV, content=content)

def NAND(a, b, content=None):
    api.add_operator("NAND", [a, b], ctx, PROV, content=content)

# ── Q1: Shared state as episodic implementation vs separate coordination layer ──

print("=== Q1 ===")
q1_unified   = P("[CONFIDENCE:HIGH] Unified pipeline — shared state IS memory: event log serves as both coordination backbone and agent episodic memory. Single source of truth, replay-enables debugging, simpler operational surface. Evidence: MAN+ESM architecture uses event log for both state reconstruction and agent choreography. Trade-off: coordination events and memory events share same schema — tight coupling, harder to tune independently. See: Kafka as both event bus and message queue pattern.")
q1_separate  = P("[CONFIDENCE:HIGH] Separate layers — coordination events (low-latency, choreography) and knowledge events (enrichment, long-term) have different SLAs, processing patterns, and schemas. Evidence: event-driven architecture best practice is to separate operational planes from reasoning planes. Coordination is irregular/asynchronous; knowledge processing follows pipeline pattern. Mixing creates architectural friction.")
q1_sla       = P("[CONFIDENCE:MEDIUM] Different SLAs: coordination needs immediate response (saga pattern, distributed transactions); knowledge events tolerate batching and higher latency. Evidence: event maturity models distinguish raw→validated→enriched→curated, which coordination events skip entirely.")
q1_unified_benefit  = P("[CONFIDENCE:MEDIUM] Unified pipeline benefit: cross-referencing. When coordination events and knowledge events live in the same log, you can ask 'what knowledge was available when this coordination decision was made?' — impossible if they're in separate pipelines. This is the audit/time-travel advantage.")

IMPL(q1_unified, q1_unified_benefit, "unified pipeline enables cross-referencing")
NAND(q1_unified, q1_separate, "unified vs separate: contradictory architectures")
NAND(q1_unified, q1_sla, "unified pipeline violates SLA differentiation")
IMPL(q1_separate, q1_sla, "separate layers justified by SLA divergence")

# ── Q2: One event pipeline vs multiple ──

print("=== Q2 ===")
q2_single   = P("[CONFIDENCE:HIGH] Single unified event pipeline: one append-only log for all event types. Cross-referencing is free, schema evolution is centralized, debugging is simpler — one place to look. Evidence: unified event logging reduces O(n²) integration explosion; Kafka acts as system-wide event log. Trade-off: single schema constrains domain-specific optimization; all consumers share one retention policy.")
q2_multi    = P("[CONFIDENCE:HIGH] Multiple domain-specific pipelines: one event store per bounded context (microservice pattern). Each pipeline optimizes for its domain — different retention, different schemas, independent scaling. Evidence: standard microservice pattern; each service owns its event store. Trade-off: cross-pipeline correlation requires explicit joins; schema divergence risk.")
q2_correlation = P("[CONFIDENCE:HIGH] Cross-pipeline correlation is the killer trade-off. Single pipeline makes correlation trivial (same log, same ids). Multiple pipelines require correlation ids and join logic — but that's already the microservice standard pattern, solved with distributed tracing (Flow IDs, trace context). Evidence: distributed tracing is battle-tested; single-pipeline correlation is a convenience, not a necessity.")
q2_isolation = P("[CONFIDENCE:MEDIUM] Isolation benefit: separate pipelines can have different security boundaries (PII vs non-PII), different durability guarantees, different compaction strategies. Evidence: compliance partitioning is standard in event-driven systems. Single pipeline forces all events to share the same policy.")

NAND(q2_single, q2_multi, "single vs multiple: contradictory architectures")
IMPL(q2_multi, q2_isolation, "multiple pipelines enable isolation")
IMPL(q2_single, q2_correlation, "single pipeline simplifies correlation")
NAND(q2_single, q2_isolation, "single pipeline prevents domain isolation")

# ── Q3: JSONL vs FalkorDB as source of truth ──

print("=== Q3 ===")
q3_jsonl    = P("[CONFIDENCE:HIGH] JSONL as source of truth: append-only file, zero dependencies, grep-able, replay-friendly, git-diffable. Every event is a self-contained JSON line. The current state is a projection (fold). Evidence: Event sourcing canonical pattern — the log is the database. Trade-off: querying requires full replay or external index; compaction is manual; no built-in concurrency control.")
q3_falkor   = P("[CONFIDENCE:HIGH] FalkorDB as source of truth: graph-native with openCypher queries, indexed traversal, GraphBLAS sparse-matrix engine. Points and operators live as nodes and typed edges. Evidence: FalkorDBLite is embedded (zero-config), same graph portable to server FalkorDB. Trade-off: requires a running database; schema evolution is more constrained; the graph is a snapshot, not a replayable log.")
q3_hybrid   = P("[CONFIDENCE:HIGH] Hybrid: JSONL is the source of truth; FalkorDB is a derived projection. Already the Tortoise design. Event log is append-only, immutable, replayable. FalkorDB is rebuilt from the log on demand. Evidence: This is the architecture Tortoise committed to (2026-07-07). Trade-off: eventual consistency between log and projection; rebuild cost grows with log size.")
q3_replay   = P("[CONFIDENCE:HIGH] Replay is the JSONL advantage. You can re-extract with a different model version and append new points without losing the old ones — the log is an immutable ledger. FalkorDB-only stores would require migration scripts. Evidence: Tortoise idempotency design (input-keyed, version-superseded replay). This is the core argument for log-first.")
q3_query    = P("[CONFIDENCE:MEDIUM] Query is the FalkorDB advantage. graph-native queries (pathfinding, centrality, grounding computation via GraphBLAS) are impossible against raw JSONL without an index. But the projection pattern already solves this: query FalkorDB, trust the log.")

IMPL(q3_hybrid, q3_jsonl, "hybrid preserves JSONL as source of truth")
IMPL(q3_hybrid, q3_falkor, "hybrid uses FalkorDB for query")
IMPL(q3_jsonl, q3_replay, "JSONL enables replay via append-only immutability")
IMPL(q3_falkor, q3_query, "FalkorDB enables graph-native queries")
NAND(q3_jsonl, q3_falkor, "mutual exclusivity as SOT: you pick one canonical store")

# ── Q4: One Tortoise instance vs per-agent instances ──

print("=== Q4 ===")
q4_single   = P("[CONFIDENCE:HIGH] Single shared Tortoise/FalkorDB instance: all agents write to the same graph. Unified queries, cross-referencing free, single grounding computation across all agent contributions. Evidence: centralized knowledge graph is simplest — one schema, one integration point. Trade-off: write contention at scale; single point of failure; privacy boundaries require namespace isolation.")
q4_peragent = P("[CONFIDENCE:HIGH] Per-agent Tortoise instances: each agent owns its own log+projection. Isolation by default — no write contention, agents can use different extractor models, independent scaling. Evidence: federated knowledge graphs (FedE) aggregate local embeddings without sharing raw triples. Trade-off: cross-agent queries require federation layer; duplicate detection across agents is hard.")
q4_hybrid_kg = P("[CONFIDENCE:HIGH] Hybrid: per-agent local graphs with selective sync to shared layer. Agents operate autonomously on private memory; validate results, then push to governed shared context. Evidence: emerging consensus in multi-agent memory — 'global by default with namespace isolation for sensitive contexts.' Trade-off: sync protocol complexity; eventual consistency between local and shared.")
q4_contention = P("[CONFIDENCE:MEDIUM] Write contention: single FalkorDB instance becomes a bottleneck when N agents append concurrently. The append-only log IS contention-free (sequential writes), but FalkorDB graph writes under concurrent projection are not. Evidence: centralized KG faces context saturation at orchestrator; per-agent avoids this by design.")
q4_crossref  = P("[CONFIDENCE:MEDIUM] Cross-referencing benefit: single instance makes cross-agent queries trivial — 'what did Agent B say about X when Agent A decided Y?' Requires explicit federation in per-agent model. However, Tortoise provenance already tags speaker+source, so a federated query layer could reconstruct this from per-agent logs.")

IMPL(q4_single, q4_crossref, "single instance enables trivial cross-referencing")
NAND(q4_single, q4_peragent, "single vs per-agent: contradictory architectures")
IMPL(q4_peragent, q4_contention, "per-agent avoids write contention")
IMPL(q4_hybrid_kg, q4_single, "hybrid preserves shared layer for coordination")
IMPL(q4_hybrid_kg, q4_peragent, "hybrid preserves per-agent isolation")

# ── Cross-question connections ──

print("=== Cross-Q ===")
# Q3 (JSONL as SOT) supports Q2 (single pipeline easier with file-based log)
IMPL(q3_jsonl, q2_single, "JSONL log-first naturally supports single pipeline")
# Q1 (separate layers) supports Q2 (multiple pipelines map to separate coordination/knowledge)
IMPL(q1_separate, q2_multi, "separate layers naturally map to multiple pipelines")
# Q4 (per-agent) works better with Q3 (JSONL per-agent = zero-deps, no DB contention)
IMPL(q4_peragent, q3_jsonl, "per-agent instances favor zero-dep JSONL over DB")
# Q4 (single) works better with Q3 (FalkorDB) for unified queries
IMPL(q4_single, q3_falkor, "single shared instance benefits from graph-native queries")
# Q1 unified + Q2 single form a coherent "simplicity-first" cluster
IMPL(q1_unified, q2_single, "unified pipeline aligns with single event stream philosophy")

print(f"\nDone. Log: {LOG_PATH}, DB: {DB_PATH}")
print("Verify: python3 -c \"from tortoise.projection import FalkorProjection; "
      "p=FalkorProjection('tortoise.db'); "
      "print(p.g.query('MATCH (n:Point) RETURN count(n)').result_set)\"")
