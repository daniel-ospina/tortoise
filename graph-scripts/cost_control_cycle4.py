"""Cycle 4 — Convergence: Synthesis point with phased recommendation."""
# Historical — uses embedded tortoise.db. Do not run against production Docker.
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tortoise.api import EventAPI, provenance
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection

log = EventLog('cost-control-cycle4.jsonl')
proj = FalkorProjection('tortoise.db')
api = EventAPI(log, initiated_by="user", agent_id="research-agent", projection=proj)

pv = lambda quote: provenance("cost-control-convergence", (0,0), quote, speaker="research-agent", extracted_by="manual@1.0")
ctxt = "cost-control"

api._emit("ingest_begin", source_id="cost-control-convergence-cycle4", extractor_version="manual@1.0")

# ════════════════════════════════════════════════════════════════════════════
# SYNTHESIS: Phased cost control strategy for epistemic graphs
# ════════════════════════════════════════════════════════════════════════════

s1 = api.add_point(
    "[CONFIDENCE:HIGH] SYNTHESIS: Cost control for epistemic graphs requires a 3-phase build-out "
    "sequenced by architectural dependency, not by feature desirability. Each phase unlocks the "
    "next. The core insight: cost control is not an add-on feature — it is the ARCHITECTURE of "
    "how the graph processes, notifies, and charges. Build the architecture, and cost control "
    "falls out as a property of the system.",
    ctxt, pv("SYNTHESIS: 3-phase build-out. Cost control = architecture property, not feature."))

# ════════════════════════════════════════════════════════════════════════════
# PHASE 1: Foundation (Build Now) — The Minimum Viable Cost Architecture
# ════════════════════════════════════════════════════════════════════════════

s2 = api.add_point(
    "[CONFIDENCE:HIGH] PHASE 1 — FOUNDATION (Build Now): "
    "(a) QUEUE-FIRST EVENT PIPELINE: Every graph mutation goes through a persistent event log "
    "(Tortoise JSONL already exists) → Kafka/SQS → Webhook Worker. "
    "This is NOT new infrastructure — it's extracting the existing EventLog into a delivery pipeline. "
    "(b) FILTER-BASED SUBSCRIPTIONS: Subscribe to point/operator changes with filters: "
    "context='climate-policy', op_type='NAND', grounding>0.7. "
    "Store as Subscription nodes in the same FalkorDB graph. "
    "(c) BOUNDED GROUNDING: Push-limited N-hop grounding from changed nodes (already have "
    "FalkorProjection.compute_grounding — add N-hop parameter). "
    "(d) FREEMIUM TIER: 5 subscriptions free, read-only queries free, 1 branch free. "
    "Auto-pause after 72h inactivity. "
    "RATIONALE: Phase 1 establishes the cost architecture without pricing complexity. "
    "Every graph change flows through the pipeline; cost tracing is natural.",
    ctxt, pv("PHASE 1: Queue-first events + filter subscriptions + bounded grounding + freemium"))

s3 = api.add_point(
    "[CONFIDENCE:HIGH] PHASE 1 IMPLEMENTATION: Largely exists already. "
    "Tortoise EventAPI writes to JSONL log (EXISTS). FalkorProjection maintains the graph "
    "(EXISTS). compute_grounding exists (EXISTS). "
    "What's NEW: (1) Webhook delivery from EventLog tail → HTTP POST. "
    "(2) Subscription filter model (Subscription node with filter expression). "
    "(3) N-hop parameter on compute_grounding. "
    "(4) Tier metadata on graph operations for future cost tracking. "
    "Estimated effort: 3-5 days of focused development. Most components are wiring, not building.",
    ctxt, pv("PHASE 1 effort: ~3-5 days; most components exist, need wiring not building"))

# ════════════════════════════════════════════════════════════════════════════
# PHASE 2: Economics Layer (Build Next) — Pricing + Branch Management
# ════════════════════════════════════════════════════════════════════════════

s4 = api.add_point(
    "[CONFIDENCE:MEDIUM] PHASE 2 — ECONOMICS (Build Next): "
    "(a) BRANCH MERGE CRITERIA: Auto-merge when (i) branch has no open NAND conflicts AND "
    "(ii) grounding scores have stabilized (Δ < 0.05 for 3 consecutive updates). "
    "Manual override for human-flagged epistemic disputes. "
    "(b) PRO/TEAM TIERS: Implement 4-tier pricing (Free/Pro/Team/Enterprise from v9). "
    "Tier enforcement at the API layer: subscription count, branch count, grounding depth, storage. "
    "(c) FULL GLOBAL GROUNDING: Scheduled (nightly/weekly), not on every change. "
    "This is the 'expensive' computation that paid tiers unlock. "
    "(d) COST TRACKING DASHBOARD: Per-subgraph compute-hour consumption, storage GB, subscription count. "
    "Thematic budget allocation UI: assign GB-hours/month to areas. "
    "RATIONALE: Phase 2 makes Phase 1 monetizable. Branch merge criteria prevent graph fragmentation. "
    "Full grounding provides accuracy for paying customers.",
    ctxt, pv("PHASE 2: Branch merge + Pro/Team tiers + full grounding + cost dashboard"))

s5 = api.add_point(
    "[CONFIDENCE:MEDIUM] PHASE 2 DEPENDENCIES: Phase 2 REQUIRES Phase 1 as foundation. "
    "Branch merge criteria depend on subscription notifications (merge triggers webhook). "
    "Tiered pricing depends on the cost tracking metadata laid down in Phase 1. "
    "Full grounding depends on bounded grounding being stable (Phase 1 validates the approach). "
    "Cannot skip Phase 1 to build Phase 2 — the cost tracking IS the architecture.",
    ctxt, pv("PHASE 2 depends on Phase 1 foundation — cannot skip; sequential dependency"))

# ════════════════════════════════════════════════════════════════════════════
# PHASE 3: Optimization (Build Later) — Advanced Features
# ════════════════════════════════════════════════════════════════════════════

s6 = api.add_point(
    "[CONFIDENCE:MEDIUM] PHASE 3 — OPTIMIZATION (Build Later): "
    "(a) CROSS-WING TUNNELS: Subscription filters that span multiple wings. "
    "'Alert me when any auto-discovery finding contradicts a cost-control conclusion.' "
    "Requires Phase 2 branch model to handle cross-branch contradictions. "
    "(b) PREDICTIVE COST ALERTS: ML-based forecasting of compute cost per thematic area. "
    "'Your climate-policy budget will be exhausted in 3 days at current rate.' "
    "(c) ENTERPRISE FEATURES: Custom SLAs, dedicated compute, multi-tenant isolation. "
    "(d) DELTA QUERIES: Token-based catch-up for missed notifications (p4 validated). "
    "RATIONALE: Phase 3 is speculative value — build only when Phase 1+2 revenue justifies it. "
    "These features have unclear ROI without user data from Phase 2.",
    ctxt, pv("PHASE 3: Cross-wing tunnels + predictive alerts + enterprise + delta queries"))

# ════════════════════════════════════════════════════════════════════════════
# CONVERGENCE: Key architectural decisions
# ════════════════════════════════════════════════════════════════════════════

s7 = api.add_point(
    "[CONFIDENCE:HIGH] DECISION: Push-first, pull-fallback. The architecture pushes "
    "notifications via webhooks (queue-first for reliability). Pull (polling API) is the "
    "fallback for offline consumers and free tier users who exceed subscription caps. "
    "This is HYBRID — not push OR pull, but push WITH pull fallback. "
    "The decision is settled: the hybrid architecture (p19) is correct and implementable.",
    ctxt, pv("DECISION: Push-first with pull fallback = hybrid architecture confirmed"))

s8 = api.add_point(
    "[CONFIDENCE:HIGH] DECISION: Resource-based billing, not per-operation. "
    "Graph operations vary too much in cost to price individually. The standard is "
    "compute hours + storage GB. For epistemic graphs: grounding depth tier determines "
    "compute cost (bounded vs full), subscription count determines notification cost, "
    "branch count determines storage cost. Users pay for capacity, not per-call.",
    ctxt, pv("DECISION: Resource-based billing — compute hours + storage, not per-operation"))

s9 = api.add_point(
    "[CONFIDENCE:HIGH] DECISION: Branch per hypothesis, merge on stability. "
    "Hypothesis branches isolate competing claims. Merge into main when: "
    "(i) no open NAND conflicts, (ii) grounding has stabilized (Δ < threshold for N steps). "
    "Branch merge triggers push notification to area subscribers. "
    "This is the concrete bridge between branching model and subscription architecture.",
    ctxt, pv("DECISION: Branch per hypothesis; merge on NAND-free + grounding stability"))

s10 = api.add_point(
    "[CONFIDENCE:HIGH] RECOMMENDATION: The cost control architecture is NOT a separate system. "
    "It emerges from three architectural primitives already in Tortoise: "
    "(1) Append-only event log (JSONL) — provides the audit trail for cost accounting. "
    "(2) FalkorDB graph projection — provides the queryable state for subscription filters. "
    "(3) compute_grounding — provides the computation that costs money. "
    "Adding queue delivery, filter subscriptions, and N-hop grounding parameter makes cost "
    "control a PROPERTY of the existing system, not a new system. Build Phase 1 now.",
    ctxt, pv("RECOMMENDATION: Cost control emerges from existing primitives — build Phase 1 now"))

# ════════════════════════════════════════════════════════════════════════════
# Wiring: Synthesis connections
# ════════════════════════════════════════════════════════════════════════════

# Phase 1 enables Phase 2
api.add_operator("IMPL", [s2, s4], ctxt,
    pv("Phase 1 foundation (queue + subscriptions + bounded grounding) enables Phase 2 economics"))

# Phase 2 enables Phase 3
api.add_operator("IMPL", [s4, s6], ctxt,
    pv("Phase 2 branch model and pricing enables Phase 3 cross-wing features"))

# Queue-first + filter subscriptions → hybrid push/pull
api.add_operator("IMPL", [s2, s7], ctxt,
    pv("Queue-first delivery + filter subscriptions implement the hybrid push/pull architecture"))

# Resource-based billing → tiered pricing
api.add_operator("IMPL", [s8, s4], ctxt,
    pv("Resource-based billing model enables Pro/Team tier structure"))

# Branch merge criteria → push notifications
api.add_operator("IMPL", [s9, s4], ctxt,
    pv("Branch merge-on-stability criteria trigger push notifications in Phase 2"))

# Existing primitives → cost architecture
api.add_operator("IMPL", [s10, s3], ctxt,
    pv("Cost control emerges from EventLog + FalkorProjection + compute_grounding — already built"))

# Phase 1 → Phase 2 dependency validated
api.add_operator("IMPL", [s3, s5], ctxt,
    pv("Phase 1 low-effort implementation validates the foundation for Phase 2"))

# ════════════════════════════════════════════════════════════════════════════
# NAND: What NOT to build
# ════════════════════════════════════════════════════════════════════════════

# Per-operation pricing vs resource-based
api.add_operator("NAND", [s8, s4], ctxt,
    pv("Per-operation pricing is appealing but impractical — resource-based is the industry standard"))

# Phase 3 before Phase 1+2
api.add_operator("NAND", [s6, s2], ctxt,
    pv("Phase 3 features require Phase 1+2 foundation; building out of order creates dead code"))

# Full global grounding on every change
api.add_operator("NAND", [s4, s2], ctxt,
    pv("Full global grounding on every change is cost-prohibitive; scheduled full + continuous bounded"))

api._emit("ingest_end", source_id="cost-control-convergence-cycle4")
print(f"Cycle 4 complete: {10} synthesis points + {7} IMPL + {3} NAND edges filed")
