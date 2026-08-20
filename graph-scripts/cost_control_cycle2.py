"""Cycle 2 — Gap Analysis: Query graph, identify weak points and missing connections."""
# Historical — uses embedded tortoise.db. Do not run against production Docker.
import sys, os  # noqa: E401, I001
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tortoise.api import EventAPI, provenance
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection

log = EventLog('cost-control-cycle2.jsonl')
proj = FalkorProjection()
api = EventAPI(log, initiated_by="user", agent_id="research-agent", projection=proj)

pv = lambda quote: provenance("cost-control-gap-analysis", (0,0), quote, speaker="research-agent", extracted_by="manual@1.0")  # noqa: E731
ctxt = "cost-control"

api._emit("ingest_begin", source_id="cost-control-gap-analysis-cycle2", extractor_version="manual@1.0")

# ════════════════════════════════════════════════════════════════════════════
# GAP A: Push reliability mechanism for epistemic graphs
# ════════════════════════════════════════════════════════════════════════════

g1 = api.add_point(
    "[CONFIDENCE:LOW] GAP-A: Push reliability mechanism for epistemic graphs is undefined. "
    "We know push needs Kafka/queues (p18), and hybrid architecture is recommended (p19), "
    "but the concrete mechanism is missing: how does a subscription fire when a critical node "
    "changes confidence? What's the fallback from push to pull when the consumer is offline? "
    "How to implement delta queries as catch-up for epistemic operators (NAND/IMPL changes), "
    "not just entity updates? Required: concrete event schema for epistemic change notifications.",
    ctxt, pv("GAP: Push reliability mechanism undefined — event schema, fallback, delta for operators"))

g2 = api.add_point(
    "[CONFIDENCE:LOW] GAP-A2: Subscription granularity for epistemic graphs. "
    "Per-node subscription (1 node changes → 1 notification) risks alert fatigue. "
    "Per-subgraph subscription (any change in 'climate policy' subgraph) may be too coarse. "
    "Per-query-type subscription (any NAND operator added to my area) is a middle ground. "
    "What granularity levels make economic and cognitive sense?",
    ctxt, pv("GAP: Subscription granularity undefined — per-node vs per-subgraph vs per-query-type"))

# ════════════════════════════════════════════════════════════════════════════
# GAP B: Branch merge criteria and cost model
# ════════════════════════════════════════════════════════════════════════════

g3 = api.add_point(
    "[CONFIDENCE:LOW] GAP-B: Branch merge criteria for epistemic graph branches. "
    "Proposal (p22): branch per hypothesis, NAND as merge conflict. But what triggers a merge? "
    "Automatic merge when a hypothesis branch has no open NAND conflicts? Manual sign-off? "
    "Merge when grounding score stabilizes (Δ < threshold for N steps)? "
    "Cost of maintaining isolated branches vs cost of premature merge (coherence damage).",
    ctxt, pv("GAP: Merge criteria undefined — conflict-resolution, grounding-stability, manual vs auto"))

g4 = api.add_point(
    "[CONFIDENCE:LOW] GAP-B2: Branch isolation cost. Each branch duplicates the graph subset "
    "it touches. Forks multiply storage. Without merge criteria, branches accumulate. "
    "The Git analogy breaks: Git stores diffs efficiently (packfiles), but epistemic graph "
    "branches store competing claims — not diffs. Storage cost of branch proliferation is unknown.",
    ctxt, pv("GAP: Branch isolation cost unknown — not like Git diffs; competing claims ≠ delta storage"))

# ════════════════════════════════════════════════════════════════════════════
# GAP C: Cost accounting model for graph operations
# ════════════════════════════════════════════════════════════════════════════

g5 = api.add_point(
    "[CONFIDENCE:LOW] GAP-C: No cost accounting model for epistemic graph operations. "
    "We have thematic budget categories (p10) and incremental processing savings (p13-p14), "
    "but no MODEL mapping operations to costs. What does add_point cost vs add_operator? "
    "What does compute_grounding cost vs traverse? Without a cost model, thematic budgets "
    "are allocative hand-waving — we can't prioritize if we can't price.",
    ctxt, pv("GAP: Cost accounting model missing — operation→cost mapping needed for budget allocation"))

g6 = api.add_point(
    "[CONFIDENCE:LOW] GAP-C2: Incremental grounding cost is theoretically unbounded for "
    "traversal queries (p15). But grounding IS a traversal operation (PageRank-style). "
    "If grounding cannot be made incremental, it becomes the cost bottleneck — "
    "every new point changes global grounding scores. How to bound incremental grounding? "
    "Approximate methods (push-limited, damped)? Lazy recomputation?",
    ctxt, pv("GAP: Incremental grounding cost — if grounding is global, it dominates all other costs"))

# ════════════════════════════════════════════════════════════════════════════
# GAP D: Cross-topic wiring deficits
# ════════════════════════════════════════════════════════════════════════════

g7 = api.add_point(
    "[CONFIDENCE:HIGH] GAP-D: Cross-topic wiring deficit — Subscription model (Topic 1) and "
    "Push/Pull processing (Topic 5) are structurally identical problems viewed from different "
    "angles. Subscription = 'who gets notified', Push/Pull = 'how data flows'. They need "
    "a unified architecture: subscription defines the WHAT, push/pull defines the HOW. "
    "Currently disconnected in the graph.",
    ctxt, pv("GAP: Subscription and Push/Pull are disconnected — need unified architecture"))

g8 = api.add_point(
    "[CONFIDENCE:HIGH] GAP-D2: Thematic Budgets (Topic 3) and Cost Scaling (Topic 4) are "
    "disconnected — the budget model says 'prioritize what matters' but never connects to "
    "'what does it cost to compute what matters?'. A graph area with high strategic value "
    "may have high computational cost (dense edges, frequent updates). Budget allocation "
    "without cost awareness is blind prioritization.",
    ctxt, pv("GAP: Budgets and Cost scaling disconnected — priority without cost awareness is blind"))

g9 = api.add_point(
    "[CONFIDENCE:MEDIUM] GAP-D3: Branching model (Topics 2/6) and Push/Pull (Topic 5) are "
    "disconnected. A branch merge IS a push event — when a hypothesis graduates to main, "
    "subscribers to that area should be notified. Conversely, branch isolation may reduce "
    "push noise: only merged conclusions trigger notifications, not every branch-internal edit. "
    "The branching model is the notification batching mechanism.",
    ctxt, pv("GAP: Branching and Push/Pull disconnected — branch merge = notification event"))

# ════════════════════════════════════════════════════════════════════════════
# GAP E: Economic model for epistemic graph subscriptions
# ════════════════════════════════════════════════════════════════════════════

g10 = api.add_point(
    "[CONFIDENCE:LOW] GAP-E: Economic model for epistemic graph access. "
    "Should we charge per-node subscription (monitoring 1000 nodes = 1000× cost)? "
    "Per-computation budget (compute_grounding on subgraph = X credits)? "
    "Per-branch (maintaining a hypothesis branch = Y credits/month)? "
    "Free tier: read-only graph queries + 3 monitored nodes? "
    "The economic model shapes the architecture — expensive operations need to be bounded.",
    ctxt, pv("GAP: Economic model undefined — per-node, per-computation, per-branch pricing"))

g11 = api.add_point(
    "[CONFIDENCE:MEDIUM] GAP-E2: The 'free tier problem' — if read-only queries are free "
    "but compute_grounding is not, users will poll (pull) instead of subscribing (push). "
    "This reverses the architectural recommendation (push over pull for freshness). "
    "Economic incentives must align with architectural best practices — or the architecture "
    "will be undermined by cost-optimizing behavior.",
    ctxt, pv("GAP: Free tier creates pull bias — economic incentives must align with architecture"))

# ════════════════════════════════════════════════════════════════════════════
# WIRING: Connect orphans and cross-topic gaps
# ════════════════════════════════════════════════════════════════════════════

# Security (p5, orphan) → enables subscription architecture (p1)
api.add_operator("IMPL", [g1, g2], ctxt,
    pv("Push reliability mechanism requires subscription granularity decisions"))

# KG budget relevance (p12, orphan) → enables cost accounting model
api.add_operator("IMPL", [g5, g6], ctxt,
    pv("Cost accounting model must map thematic budget priorities to computational costs"))

# Push reliability (g1) needs the subscription model (p1-p5)
api.add_operator("IMPL", [g1, g7], ctxt,
    pv("Push reliability mechanism bridges subscription model and push/pull architecture"))

# Branch merge criteria (g3) → subscription notification (g9)
api.add_operator("IMPL", [g3, g9], ctxt,
    pv("Branch merge criteria define when push notifications fire for graph changes"))

# Incremental grounding (g6) → cost scaling (p13)
api.add_operator("IMPL", [g6, g5], ctxt,
    pv("Incremental grounding cost is the bottleneck that the cost accounting model must address"))

# Economic model (g10) → bridges budget categories (p10) and cost scaling (p13)
api.add_operator("IMPL", [g10, g11], ctxt,
    pv("Economic model must resolve the free-tier pull bias against architectural push recommendations"))

# ════════════════════════════════════════════════════════════════════════════
# NAND: Tensions exposed by gap analysis
# ════════════════════════════════════════════════════════════════════════════

# Branch isolation vs notification batching
api.add_operator("NAND", [g4, g9], ctxt,
    pv("Branch isolation reduces notification noise but increases storage cost — tension"))

# Free tier (pull bias) vs architectural recommendation (push)
api.add_operator("NAND", [g11, g7], ctxt,
    pv("Free-tier economics incentivizes pull; architectural best practice recommends push"))

# Per-node subscription vs alert fatigue
api.add_operator("NAND", [g2, g1], ctxt,
    pv("Fine-grained subscriptions enable precision but risk alert fatigue at scale"))

# Thematic priority vs computational cost
api.add_operator("NAND", [g8, g5], ctxt,
    pv("High-priority areas may have high computational cost — budget must reconcile both"))

api._emit("ingest_end", source_id="cost-control-gap-analysis-cycle2")
print(f"Cycle 2 complete: {11} gap points + {6} IMPL + {4} NAND edges filed")
