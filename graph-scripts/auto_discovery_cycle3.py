"""Cycle 3 — Validation: Research gaps, file evidence, update confidence."""
# Historical — uses embedded tortoise.db. Do not run against production Docker.
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tortoise.api import EventAPI, provenance
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection

log = EventLog('auto-discovery-cycle3.jsonl')
proj = FalkorProjection()
api = EventAPI(log, initiated_by="user", agent_id="research-agent", projection=proj)

pv = lambda quote: provenance("auto-discovery", (0,0), quote, speaker="research-agent", extracted_by="manual@1.0")

api._emit("ingest_begin", source_id="gap-validation", extractor_version="manual@1.0")

# ── Evidence for Gap 1: Batch/Streaming Bridge ───────────────────────────
e1 = api.add_point(
    "[CONFIDENCE:HIGH] Hybrid Lambda/Kappa architecture resolves batch vs streaming tension: "
    "batch layer (cold path, hours/days, deep reasoning), speed layer (hot path, <1s, "
    "incremental extraction), serving layer (merges both for unified queries). Pattern is "
    "well-established: live KG construction links streaming entity references to stable graph. "
    "Gap 1 is RESOLVED — architecture pattern exists.",
    "auto-discovery",
    pv("Hybrid batch/streaming: Lambda architecture with hot/cold paths resolves contradiction"))

e2 = api.add_point(
    "[CONFIDENCE:HIGH] Real-time entity resolution via lightweight projector networks: "
    "maps streaming entity features to embeddings for immediate cluster assignment without "
    "retraining or graph reconstruction. Change Data Capture (CDC) keeps graph as 'mirror of "
    "reality.' Temporal context tracking (Graphiti) maintains provenance across time.",
    "auto-discovery",
    pv("Real-time ER + CDC + temporal tracking enable streaming edge discovery at scale"))

# ── Evidence for Gap 3: Predictive Multiplicity Resolution ────────────────
e3 = api.add_point(
    "[CONFIDENCE:HIGH] Range Voting (social choice theory) resolves predictive multiplicity: "
    "aggregates individual entity rankings into collective preference, reducing conflicting "
    "predictions by 66-78% while maintaining/improving Hit@K. Significantly outperforms "
    "Borda voting, majority voting, and simple averaging. Gap 3 is RESOLVED.",
    "auto-discovery",
    pv("Range Voting reduces predictive multiplicity by 66-78%, maintains accuracy"))

e4 = api.add_point(
    "[CONFIDENCE:HIGH] Ensemble strategy for link prediction: (1) Range Voting for multiplicity "
    "resolution, (2) Adaptive Weighting (WeightedKgBlend) for noise robustness, (3) Attention "
    "mechanisms for dynamic model selection per query type. Combined approach handles both "
    "multi-model disagreement AND noisy data.",
    "auto-discovery",
    pv("Two-layer ensemble: Range Voting + Adaptive Weighting + Attention for link prediction"))

# ── Evidence for Gap 5: Incremental + Cross-Domain Scaling ────────────────
e5 = api.add_point(
    "[CONFIDENCE:HIGH] FAMER (2024): incremental clustering with cluster repair for multi-source "
    "entity resolution. Order-independent results with batch-like quality. Solves the incremental "
    "update problem without full reprocessing across heterogeneous sources.",
    "auto-discovery",
    pv("FAMER: incremental clustering + cluster repair, order-independent"))

e6 = api.add_point(
    "[CONFIDENCE:HIGH] IncRML (2024): CDC-based declarative incremental KG construction from "
    "heterogeneous sources. Handles changing data sources with versioning support. EAGER: first "
    "graph-embedding-supported ER handling multiple entity types simultaneously without schema matching.",
    "auto-discovery",
    pv("IncRML + EAGER: incremental KG construction + embedding-based multi-type ER"))

e7 = api.add_point(
    "[CONFIDENCE:MEDIUM] Hybrid LLM+Rule pipeline (2024-2025): LLM for ontology induction + "
    "rule-based IE and ER for cross-domain scalability. Only configuration changes needed for "
    "domain transfer. Embedding-based blocking avoids quadratic pairwise comparisons. "
    "Gap 5 is PARTIALLY RESOLVED — approaches exist but production deployment evidence is thin.",
    "auto-discovery",
    pv("Hybrid LLM+Rule: ontology induction + rule-based IE; config-only domain transfer"))

# ── Additional: Cross-domain embedding mapping evidence (Gap 2) ───────────
e8 = api.add_point(
    "[CONFIDENCE:MEDIUM] Embedding-based blocking for cross-domain ER: approximate nearest "
    "neighbor search maps entities into shared embedding space without explicit schema alignment. "
    "LLM-driven zero-shot ER being tested for stability. Pre-KG resolution (resolve before "
    "ingestion) emerging as best practice. Gap 2 is PARTIALLY RESOLVED — techniques exist "
    "but cross-domain embedding transfer remains an active research area.",
    "auto-discovery",
    pv("ANN search + zero-shot LLM ER + pre-KG resolution for cross-domain mapping"))

# ── IMPL edges from evidence to approaches ───────────────────────────────
# Hybrid architecture → resolves batch/streaming NAND
api.add_operator("IMPL", [e1, e2], "auto-discovery",
    pv("Hybrid Lambda architecture enables both batch deep reasoning and streaming real-time edges"))

# Real-time ER → enables push-based ingest-time discovery
api.add_operator("IMPL", [e2, e1], "auto-discovery",
    pv("Real-time ER + CDC provides the speed layer for hybrid architecture"))

# Range Voting → resolves predictive multiplicity
api.add_operator("IMPL", [e3, e4], "auto-discovery",
    pv("Range Voting is the primary mechanism; ensemble strategy wraps it with weighting"))

# Two-layer ensemble → improves link prediction confidence
api.add_operator("IMPL", [e4, e3], "auto-discovery",
    pv("Ensemble strategy combines voting with adaptive weighting for robust link prediction"))

# FAMER/IncRML → enables incremental cross-domain scaling
api.add_operator("IMPL", [e5, e6], "auto-discovery",
    pv("FAMER and IncRML together provide incremental + multi-source ER capabilities"))

# EAGER → cross-domain embedding ER
api.add_operator("IMPL", [e6, e7], "auto-discovery",
    pv("EAGER's embedding-based multi-type ER feeds into hybrid LLM+Rule pipelines"))

# Embedding blocking → cross-domain mapping
api.add_operator("IMPL", [e8, e7], "auto-discovery",
    pv("Embedding-based blocking enables cross-domain ER in hybrid pipelines"))

# ── Update confidence: revise gap points with new evidence ───────────────

# Query gap point IDs
gaps = proj.query(
    "MATCH (p:Point) WHERE p.context = 'auto-discovery' AND p.content STARTS WITH '[CONFIDENCE:' "
    "AND p.content CONTAINS 'GAP:' RETURN p.id, p.content"
).result_set

for pid, content in gaps:
    if "batch vs streaming" in content.lower() or "batch offline" in content.lower():
        api.revise_point(pid,
            new_content=content.replace("[CONFIDENCE:MEDIUM]", "[CONFIDENCE:LOW]")
                         .replace("no bridging approach identified", "bridging approach identified (hybrid Lambda)")
                         + " [UPDATED: Hybrid Lambda architecture resolves this — see Cycle 3 evidence]",
            corrects=None)
    elif "predictive multiplicity" in content.lower():
        api.revise_point(pid,
            new_content=content.replace("[CONFIDENCE:MEDIUM]", "[CONFIDENCE:LOW]")
                         .replace("no resolution mechanism", "resolution mechanism found (Range Voting + ensemble)")
                         + " [UPDATED: Range Voting resolves this — see Cycle 3 evidence]",
            corrects=None)
    elif "incremental scaling" in content.lower() and "cross-domain" in content.lower():
        api.revise_point(pid,
            new_content=content.replace("[CONFIDENCE:MEDIUM]", "[CONFIDENCE:LOW]")
                         .replace("no research addresses", "research exists (FAMER, IncRML, EAGER)")
                         + " [UPDATED: Multiple approaches exist — see Cycle 3 evidence]",
            corrects=None)
    elif "cross-domain embedding mapping" in content.lower():
        api.revise_point(pid,
            new_content=content.replace("[CONFIDENCE:LOW]", "[CONFIDENCE:MEDIUM]")
                         .replace("no supporting evidence chain", "partial evidence chain (ANN, zero-shot ER, pre-KG resolution)")
                         + " [UPDATED: Techniques exist, active research area]",
            corrects=None)
    elif "dual-mode" in content.lower() or "conversational ai" in content.lower():
        api.revise_point(pid,
            new_content=content.replace("[CONFIDENCE:LOW]", "[CONFIDENCE:LOW]")
                         + " [UPDATED: Still no concrete implementation evidence from Cycle 3]",
            corrects=None)
    elif "'dreaming' mode" in content.lower() or "dreaming" in content.lower():
        api.revise_point(pid,
            new_content=content.replace("[CONFIDENCE:LOW]", "[CONFIDENCE:LOW]")
                         + " [UPDATED: Still no dedicated approach found in Cycle 3]",
            corrects=None)

api._emit("ingest_end", source_id="gap-validation")
print(f"Cycle 3 complete: 8 evidence points, 7 IMPL edges, confidence revisions applied")
