"""Cycle 4 — Convergence: final tiered architecture recommendation with cost estimates."""
# Historical — uses embedded tortoise.db. Do not run against production Docker.
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tortoise.api import EventAPI, provenance
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection

log = EventLog('bp-approach-cycle4.jsonl')
proj = FalkorProjection()
api = EventAPI(log, initiated_by="user", agent_id="research-agent", projection=proj)

pv = lambda quote: provenance("bp-approach-convergence", (0,0), quote, speaker="research-agent", extracted_by="manual@1.0")
ctxt = "bp-approach"

api._emit("ingest_begin", source_id="bp-approach-cycle4", extractor_version="manual@1.0")

# ════════════════════════════════════════════════════════════════════════════
# CONVERGED ARCHITECTURE: Tiered Belief Propagation
# ════════════════════════════════════════════════════════════════════════════

p1 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED] FINAL ARCHITECTURE: Three-tier belief propagation "
    "system for Tortoise epistemic graphs. Tier 0 (Freemium $20/mo): base graph ops "
    "— FalkorDB-native PageRank propagation + embedding similarity (all-MiniLM-L6-v2) "
    "+ daily dreaming consolidation. Platform cost $0. Tier 1 (Premium $100/mo): "
    "hourly propagation + scheduled LLM edge discovery on priority subgraphs (GPT-4.1 "
    "Nano, $0.00005/edge) + semantic change alerts + freshness dashboard. Platform "
    "cost <$20/mo. Tier 2 (Enterprise $500-5K/mo): dedicated FalkorDB + custom "
    "extractors + SLA + cross-ontology queries + budget LLM edge discovery. Platform "
    "cost $50-500/mo.",
    ctxt,
    pv("Converged: 3-tier (Freemium $20, Premium $100, Enterprise $500-5K); all based on validated cost data"))

# ════════════════════════════════════════════════════════════════════════════
# TIER 0: FREEMIUM — Base Graph Operations Under $20/month
# ════════════════════════════════════════════════════════════════════════════

p2 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][TIER:FREEMIUM] What's included ($20/mo): "
    "(1) Query the epistemic graph (read-only, unlimited). "
    "(2) FalkorDB-native PageRank propagation — sub-second at 400K nodes, $0 compute. "
    "(3) Embedding similarity edge suggestion — all-MiniLM-L6-v2 ($0, Apache-2.0), "
    "sparse top-50 edges per node, ANN index. "
    "(4) Daily dreaming consolidation — offline PageRank re-propagation, $0. "
    "(5) Basic confidence scores per Point. "
    "(6) Up to 10K Points (generous headroom; 4K is the next scale milestone). "
    "Platform cost: $0/month. All compute is local/FalkorDB-native.",
    ctxt,
    pv("Freemium: PageRank $0 + embeddings $0 + daily dreaming $0 = $0 platform cost; unlimited base ops"))

p3 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][TIER:FREEMIUM] What's NOT included: "
    "(1) LLM edge discovery (user can bring their own API key — user-directed spend). "
    "(2) Semantic change alerts (premium feature). "
    "(3) Propagation faster than daily. "
    "(4) Custom extractors or ontologies. "
    "(5) Cross-ontology queries. "
    "(6) Graph size >10K Points (soft cap — still works but no optimization for scale). "
    "Freemium boundary = infrastructure is free, intelligence is user-paid.",
    ctxt,
    pv("Freemium exclusions: LLM discovery, alerts, custom extractors, cross-ontology; soft cap 10K Points"))

# ════════════════════════════════════════════════════════════════════════════
# TIER 1: PREMIUM — $100/month Justified
# ════════════════════════════════════════════════════════════════════════════

p4 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][TIER:PREMIUM] What's included ($100/mo): "
    "(1) Everything in Freemium. "
    "(2) Hourly PageRank propagation (vs daily) — $0 marginal cost, value is freshness. "
    "(3) Scheduled LLM edge discovery on priority subgraphs: budget model (GPT-4.1 Nano "
    "at $0.00005/edge). Platform budget: 400K edges/month = $20/month platform cost. "
    "Covers ~13K new edges/day — sufficient for active research. "
    "(4) Semantic change alerts: confidence-shift detection + webhook notification. "
    "Platform cost: <$1/month. "
    "(5) Freshness dashboard: propagation history, confidence trends, contradiction heatmap. "
    "(6) Up to 100K Points. "
    "Total platform cost: ~$21/month. Margin: $79/month (79% — healthy SaaS margin).",
    ctxt,
    pv("Premium: hourly propagation + LLM budget $20/mo + alerts $1 = $21 cost, $79 margin; up to 100K Points"))

p5 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][TIER:PREMIUM] Value justification for $100/mo: "
    "(1) Freshness: hourly vs daily propagation = 24× faster confidence updates after "
    "new evidence. Critical for active research workflows. "
    "(2) Selective LLM: 400K edges/month of smart classification identifies important "
    "contradictions embeddings miss. At 88% embedding precision, LLM fills the 12% gap "
    "on the most important edges. "
    "(3) Alerting: semantic change alerts (not just 'something changed' — WHAT changed "
    "and WHY, based on confidence shift). No competitor offers this. "
    "(4) Competitive: Neo4j Professional at $65/mo lacks semantic alerting and LLM "
    "edge discovery. TigerGraph at $45/GB doesn't include propagation. Our premium "
    "offers unique value at a competitive price point.",
    ctxt,
    pv("Premium value: 24x freshness, LLM gap-filling, semantic alerts (unique), competitive vs Neo4j $65"))

p6 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][TIER:PREMIUM] LLM edge discovery strategy: "
    "Not all edges need LLM classification. Embedding similarity (88% precision) "
    "identifies candidate edges. LLM classifies only the top-k most impactful: "
    "(1) Edges involving resolution events (new evidence that shifts grounding), "
    "(2) Edges with embedding confidence in the 'uncertain' zone (0.3-0.7 similarity), "
    "(3) Edges that would create new NAND contradictions. "
    "This targets the 12% precision gap on the 20% of edges that matter most — "
    "2.4% of all candidate edges get LLM classification, fitting the budget.",
    ctxt,
    pv("LLM strategy: classify only top-k impactful edges (resolution events, uncertain zone, new NANDs)"))

# ════════════════════════════════════════════════════════════════════════════
# TIER 2: ENTERPRISE — Custom Graph Infrastructure
# ════════════════════════════════════════════════════════════════════════════

p7 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][TIER:ENTERPRISE] What's included ($500-5,000/mo): "
    "(1) Everything in Premium. "
    "(2) Dedicated FalkorDB instance (not shared) — ensures predictable latency at scale. "
    "(3) Custom extractors: pipeline to ingest from organization-specific sources (Slack, "
    "Notion, Confluence, custom APIs). "
    "(4) Cross-ontology query optimization: when multiple knowledge domains overlap, "
    "optimize PageRank across ontology boundaries. "
    "(5) SLA on propagation latency (guaranteed <5s for <1M nodes). "
    "(6) Higher LLM edge discovery budget: full GPT-4.1 ($0.0005/edge) for quality-critical "
    "edges, up to 1M edges/month ($500/mo platform cost). "
    "(7) Priority support + onboarding. "
    "(8) Unlimited graph size (tested to 10M+ nodes). "
    "Platform cost range: $50 (small enterprise, no LLM) to $550 (full LLM budget). "
    "Margin: 80-90%.",
    ctxt,
    pv("Enterprise: dedicated FalkorDB + custom extractors + SLA + 1M LLM edges/mo; $50-550 cost, 80-90% margin"))

# ════════════════════════════════════════════════════════════════════════════
# COST ESTIMATES AT ALL SCALES
# ════════════════════════════════════════════════════════════════════════════

p8 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][COST:400] At 400 points: Freemium covers everything. "
    "Platform cost: $0 (PageRank <1ms, embeddings <1ms, daily dreaming $0). "
    "User-directed LLM: 400 edges × $0.0005 = $0.20/session (user's API key). "
    "Premium would be wasted — no value at this scale. Enterprise irrelevant.",
    ctxt,
    pv("400 points: freemium covers all, $0 platform cost; premium has no value at this scale"))

p9 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][COST:4K] At 4,000 points: Freemium still covers "
    "base operations ($0 platform cost). Premium value emerges: hourly propagation "
    "provides 24× faster confidence updates. LLM budget: 400K edges/month at "
    "$0.00005/edge = $20/month platform cost. 4K points with top-50 edges = 200K "
    "candidate edges, LLM classifies ~4.8K most impactful (2.4%) — fits easily "
    "in 400K/month budget. Freemium soft cap (10K points) not yet reached.",
    ctxt,
    pv("4K points: freemium $0 + premium LLM $20/mo; 200K candidates → 4.8K LLM classified; premium valuable"))

p10 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][COST:40K] At 40,000 points: Freemium hits soft "
    "cap (10K). Must upgrade to Premium. Platform cost: embeddings $0 (2M edges, "
    "<1.5GB), PageRank $0 (<1s). LLM budget: 400K edges/month still covers selective "
    "classification. 40K × top-50 = 2M candidates, LLM classifies ~48K most impactful "
    "(2.4%) — still fits. Premium's $100/mo is justified: hourly freshness matters at "
    "this scale.",
    ctxt,
    pv("40K points: freemium capped at 10K; premium must-have; 2M candidates → 48K LLM classified; $100 justified"))

p11 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][COST:400K] At 400,000 points: Premium's LLM budget "
    "is still sufficient (400K edges/month covers 2.4% of 400K × 50 = 480K, but 400K "
    "budget means ~40% coverage of impact tier — still valuable). Enterprise value: "
    "dedicated FalkorDB eliminates shared-infra latency at this scale, custom extractors "
    "for domain-specific sources, SLA on propagation (<5s even at 400K). Enterprise LLM: "
    "1M edges/month at $0.0005/edge = $500/mo platform cost. Enterprise price: $2,000/mo "
    "(80% margin) for typical deployment.",
    ctxt,
    pv("400K points: premium still works; enterprise value = dedicated infra + SLA + 1M LLM edges; $2K/mo typical"))

# ════════════════════════════════════════════════════════════════════════════
# IMPLEMENTATION PRIORITY
# ════════════════════════════════════════════════════════════════════════════

p12 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][PRIORITY:P0] Milestone 1 — Replace dense solver with "
    "sparse power iteration. Remove np.linalg.solve (O(n³)), replace with FalkorDB-native "
    "CALL algo.pageRank. This is the critical path: current implementation can't scale "
    "past 400 points. Estimated effort: 2-4 hours. No new dependencies (FalkorDBLite "
    "already installed). This alone enables freemium tier at all scales.",
    ctxt,
    pv("P0: Replace dense solver → FalkorDB PageRank; 2-4hrs; unblocks all tiers"))

p13 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][PRIORITY:P1] Milestone 2 — Add embedding similarity "
    "edge suggestion. Integrate all-MiniLM-L6-v2 (pip install sentence-transformers). "
    "Compute embeddings on PointAdded events, store as node property. Add sparse top-50 "
    "similarity query. Estimated effort: 1 day. Enables the System 1 layer of the "
    "Hybrid Lambda architecture.",
    ctxt,
    pv("P1: Add embedding similarity; 1 day; enables System 1 layer"))

p14 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][PRIORITY:P2] Milestone 3 — Implement dreaming layer "
    "(scheduled propagation). Add configurable propagation schedule (daily default). "
    "Track confidence changes between runs for alerting. Estimated effort: 2-3 days. "
    "Enables Premium tier alerting + Dreaming consolidation benefits.",
    ctxt,
    pv("P2: Scheduled propagation + confidence tracking; 2-3 days; enables Premium tier"))

p15 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][PRIORITY:P3] Milestone 4 — LLM edge discovery "
    "(user-directed + scheduled). Add EdgeDiscoveryAgent that takes embedding candidates "
    "and classifies via LLM. Support user API key for user-directed mode. Add scheduled "
    "mode for premium tier. Estimated effort: 3-5 days. Enables System 2 layer.",
    ctxt,
    pv("P3: LLM edge discovery agent; 3-5 days; enables System 2 layer"))

p16 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][PRIORITY:P4] Milestone 5 — Tier infrastructure. "
    "Rate limiting, soft caps, tier feature flags, subscription management. Estimated "
    "effort: 5-10 days. Full monetization-ready product.",
    ctxt,
    pv("P4: Tier infrastructure; 5-10 days; monetization-ready"))

# ════════════════════════════════════════════════════════════════════════════
# ARCHITECTURE DECISIONS — FINAL
# ════════════════════════════════════════════════════════════════════════════

p17 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][DECISION:FINAL] FalkorDB-native PageRank is the single "
    "propagation algorithm. No fallback to sparse power iteration needed — FalkorDBLite "
    "is embedded (no server dependency) and the benchmark evidence shows sub-second "
    "performance at all scales. Simpler, faster, proven. The 'backend flexibility' "
    "NAND is resolved: FalkorDB is the backend, period.",
    ctxt,
    pv("FINAL: FalkorDB PageRank only; no fallback; embedded = no dependency; sub-second at all scales"))

p18 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][DECISION:FINAL] all-MiniLM-L6-v2 is the embedding "
    "model. 384-dim, 22MB, Apache-2.0, 14K sent/s on CPU. No cloud API dependency. "
    "No cost. Adequate precision (88%) for edge suggestion. If higher precision needed "
    "in future, swap to all-mpnet-base-v2 (768-dim, 2× slower but higher quality) — "
    "model choice is a config parameter, not an architectural decision.",
    ctxt,
    pv("FINAL: all-MiniLM-L6-v2 embeddings; 384-dim, $0, 88% precision; swappable config"))

p19 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][DECISION:FINAL] LLM model selection is configurable "
    "per tier: Freemium = no platform LLM (user-directed only, any model via user's key). "
    "Premium = GPT-4.1 Nano ($0.00005/edge, budget). Enterprise = GPT-4.1 ($0.0005/edge, "
    "quality) with option for Claude Opus ($0.0015/edge) for critical reasoning. "
    "LLM costs are falling rapidly — architecture must treat model choice as config.",
    ctxt,
    pv("FINAL: LLM model config per tier; Nano→GPT-4.1→Opus; falling costs → configurable"))

p20 = api.add_point(
    "[CONFIDENCE:HIGH][CONVERGED][DECISION:FINAL] The economic breakpoint is not a fixed "
    "scale but a function of: (points × edge_rate × llm_cost_per_edge × impact_filter). "
    "At 88% embedding precision, LLM fills 12% gap × 20% impact tier = 2.4% of edges. "
    "Formula: monthly_llm_cost = points × top_k × 0.024 × cost_per_edge × 30. "
    "Example: 4K × 50 × 0.024 × $0.00005 × 30 = $7.20/month. Well within premium budget. "
    "Architecture should expose this formula as a cost estimator for users.",
    ctxt,
    pv("FINAL: economic breakpoint = f(points, edge_rate, llm_cost, impact_filter); formula exposed as estimator"))

# ════════════════════════════════════════════════════════════════════════════
# RISKS AND MITIGATIONS
# ════════════════════════════════════════════════════════════════════════════

p21 = api.add_point(
    "[CONFIDENCE:MEDIUM][CONVERGED][RISK] LLM cost risk: if per-edge costs don't continue "
    "falling, premium LLM budget may become insufficient at higher scales. Mitigation: "
    "(1) Embedding-only propagation is always $0 and works at any scale — LLM is an "
    "enhancement, not a requirement. (2) Configurable model selection allows trading "
    "quality for cost. (3) Impact filtering ratio can be tightened (2.4% → 1% → 0.5%). "
    "The architecture works without LLM; LLM makes it better.",
    ctxt,
    pv("RISK: LLM cost may not fall; mitigation: embeddings work alone, config model, tighten filter"))

p22 = api.add_point(
    "[CONFIDENCE:MEDIUM][CONVERGED][RISK] Embedding precision adequacy: 88% is good "
    "for edge suggestion but may create noise at large scale. Mitigation: (1) LLM "
    "classification on impact tier catches critical misses. (2) Dreaming consolidation "
    "prunes low-confidence edges over time. (3) User feedback loop (accept/reject "
    "suggested edges) provides training signal for improved precision. (4) Model "
    "swap to all-mpnet-base-v2 if precision drops below acceptable threshold.",
    ctxt,
    pv("RISK: 88% precision may be insufficient; mitigation: LLM catch + dreaming prune + user feedback"))

p23 = api.add_point(
    "[CONFIDENCE:LOW][CONVERGED][RISK] Market adoption risk: no validated willingness-to-pay "
    "data for epistemic graph propagation as a service. This is a novel category. Mitigation: "
    "(1) Freemium is generous enough to drive adoption without payment friction. "
    "(2) Premium $100/mo is below typical SaaS tools developers already pay for "
    "(GitHub Copilot $10/mo, Linear $8/mo, Notion $10/mo — combined >$30/mo). "
    "(3) Enterprise pricing is custom, negotiated per deal. "
    "(4) Wait for conversion data before optimizing pricing — launch with these tiers "
    "and adjust based on actual behavior.",
    ctxt,
    pv("RISK: unknown WTP for epistemic graph service; mitigation: generous freemium + adjust based on data"))

# ════════════════════════════════════════════════════════════════════════════
# IMPL edges — convergence validates and completes the architecture
# ════════════════════════════════════════════════════════════════════════════

# Architecture → tiers
api.add_operator("IMPL", [p1, "01KXH02YXBTS2PSYSJ1W6QMQWN"], ctxt,
    pv("Converged architecture implements the freemium decision with validated cost data"))
api.add_operator("IMPL", [p1, "01KXH02YXJ7N92121Z58T1BN64"], ctxt,
    pv("Converged architecture defines premium value proposition with cost justification"))
api.add_operator("IMPL", [p1, "01KXH02YX1Z0460TDF39PV6H32"], ctxt,
    pv("Converged architecture scopes enterprise tier with specific platform costs"))

# Tier definitions → constraints
api.add_operator("IMPL", [p2, p3, "01KXH02YWXAFE3KCF5JKCY3ZZ8"], ctxt,
    pv("Freemium tier definition satisfies $20/month constraint with $0 platform cost"))
api.add_operator("IMPL", [p4, p5, "01KXH02YWZQTZGPV10HP0P6B4D"], ctxt,
    pv("Premium tier definition justifies $100/month with concrete value and cost data"))

# Cost projections → scale points
api.add_operator("IMPL", [p8, "01KXH02YX34BWTZSV4AG8FD8FB"], ctxt,
    pv("Converged 400-scale cost: $0 platform, all in freemium"))
api.add_operator("IMPL", [p9, "01KXH02YX59PNVTS0KGCHQVNYF"], ctxt,
    pv("Converged 4K-scale cost: freemium $0 + premium LLM $20/mo"))
api.add_operator("IMPL", [p10, "01KXH02YX7XCW2ZXVYS6EXJDJE"], ctxt,
    pv("Converged 40K-scale: premium required, $100/mo justified"))
api.add_operator("IMPL", [p11, "01KXH02YX9FS6GDMXJVFGAFMZW"], ctxt,
    pv("Converged 400K-scale: enterprise at $2K/mo typical"))

# Implementation → architecture
api.add_operator("IMPL", [p12, p17], ctxt,
    pv("P0 PageRank migration enables the FalkorDB-only decision"))
api.add_operator("IMPL", [p13, p18], ctxt,
    pv("P1 embedding integration implements the chosen embedding model"))
api.add_operator("IMPL", [p15, p19], ctxt,
    pv("P3 LLM agent implements configurable model selection"))

# Economic formula → decision
api.add_operator("IMPL", [p20, p6], ctxt,
    pv("Economic formula operationalizes the selective LLM strategy"))

# Risk mitigation → architecture
api.add_operator("IMPL", [p21, p17], ctxt,
    pv("LLM cost risk mitigation: architecture works without LLM"))
api.add_operator("IMPL", [p22, p18], ctxt,
    pv("Embedding precision risk mitigation: LLM catch + dreaming prune + feedback loop"))

# ════════════════════════════════════════════════════════════════════════════
# NAND — final tensions in the converged architecture
# ════════════════════════════════════════════════════════════════════════════

api.add_operator("NAND", [p3, p6], ctxt,
    pv("Freemium exclusion of LLM vs Premium inclusion: the boundary IS the business model"))

api.add_operator("NAND", [p23, p4], ctxt,
    pv("Unknown WTP (LOW confidence) vs $100 price point: launch and adjust based on data"))

api.add_operator("NAND", [p21, p19], ctxt,
    pv("LLM cost risk vs configurable model: the config IS the mitigation; risk remains if all models get expensive"))

api._emit("ingest_end", source_id="bp-approach-cycle4")
print(f"Cycle 4 complete: {23} convergence points + {15} IMPL + {3} NAND edges filed")
