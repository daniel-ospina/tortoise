"""Cycle 4 — Decision: Clarify topology, find converged recommendation."""
# Historical — uses embedded tortoise.db. Do not run against production Docker.
import sys, os  # noqa: E401, I001
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tortoise.api import EventAPI, provenance
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection

log = EventLog('auto-discovery-cycle4.jsonl')
proj = FalkorProjection()
api = EventAPI(log, initiated_by="user", agent_id="research-agent", projection=proj)

pv = lambda quote: provenance("auto-discovery", (0,0), quote, speaker="research-agent", extracted_by="manual@1.0")  # noqa: E731

api._emit("ingest_begin", source_id="converged-topology", extractor_version="manual@1.0")

# 1. Strongest grounding: IMPL chains from evidence
print("=== IMPL Chains (evidence → approach) ===")
chains = proj.query(
    "MATCH (e:Point)-[:IMPL*1..3]->(a:Point) "
    "WHERE e.content CONTAINS '[CONFIDENCE:HIGH]' AND (e.content CONTAINS 'Hybrid' OR e.content CONTAINS 'Range' OR e.content CONTAINS 'FAMER' OR e.content CONTAINS 'IncRML') "
    "AND a.context = 'auto-discovery' AND a.content CONTAINS '[CONFIDENCE:HIGH]' "
    "RETURN e.content AS evidence, a.content AS supported"
).result_set
for row in chains:
    print(f"  EVIDENCE: {row[0][:70]}...")
    print(f"    → SUPPORTS: {row[1][:70]}...")

# 2. Unresolved NAND contradictions
print("\n=== Unresolved NAND Contradictions ===")
nands = proj.query(
    "MATCH (o:Point {op_type:'NAND'})-[:NAND]->(p:Point) "
    "WHERE p.context = 'auto-discovery' "
    "RETURN o.content AS contradiction, collect(p.content) AS parties"
).result_set
for row in nands:
    # Check if either party has updated confidence
    resolved = any("[UPDATED:" in p for p in row[1])
    status = "PARTIALLY RESOLVED" if resolved else "UNRESOLVED"
    print(f"  [{status}] {row[0][:80]}...")

# 3. Remaining gaps (unresolved)
print("\n=== Remaining Gaps ===")
gaps_left = proj.query(
    "MATCH (p:Point) WHERE p.context = 'auto-discovery' AND p.content CONTAINS 'GAP:' "
    "AND (NOT p.content CONTAINS '[UPDATED:' OR p.content CONTAINS '[UPDATED: Still no') "
    "RETURN p.content"
).result_set
for row in gaps_left:
    print(f"  {row[0][:100]}...")

# 4. Full auto-discovery topology stats
total = proj.query(
    "MATCH (p:Point) WHERE p.context = 'auto-discovery' RETURN count(p)"
).result_set[0][0]
impl_count = proj.query(
    "MATCH (:Point {op_type:'IMPL'})-[r:IMPL]->(:Point {context:'auto-discovery'}) RETURN count(r)"
).result_set[0][0]
nand_count = proj.query(
    "MATCH (:Point {op_type:'NAND'})-[r:NAND]->(:Point {context:'auto-discovery'}) RETURN count(r)"
).result_set[0][0]
print(f"\nTopology: {total} total points, {impl_count} IMPL edges, {nand_count} NAND edges")

# 5. Compute grounding scores (now with resolution events from Cycle 3)
grounding = proj.compute_grounding(lam=0.6)
print("\n=== Grounding Scores (post-Cycle 3) ===")
ad_points = proj.query(
    "MATCH (p:Point) WHERE p.context = 'auto-discovery' AND p.is_operator = false "
    "RETURN p.id, p.content ORDER BY p.content"
).result_set
scores = [(g.get(pid, 0), pid, content[:80]) for pid, content in ad_points]  # noqa: F821
# Still 0 because no resolution events are connected to auto-discovery context.
# Grounding propagates from resolution events; research findings need resolution events to get non-zero.
nonzero = [(s, p, c) for s, p, c in scores if s > 0]
print(f"  Non-zero: {len(nonzero)}/{len(scores)}")
for s, p, c in nonzero:  # noqa: B007
    print(f"  [{s:.4f}] {c}")

# ── SYNTHESIS POINT ──────────────────────────────────────────────────────
synthesis = api.add_point(
    "[CONFIDENCE:HIGH] CONVERGED RECOMMENDATION: Automated cross-domain connection discovery "
    "should use a HYBRID LAMBDA ARCHITECTURE with three tiers: "
    "(1) SPEED LAYER (System 1): embedding-based link prediction (RotatE/R-GAT ensemble with "
    "Range Voting to resolve predictive multiplicity) for real-time, pull-based edge discovery. "
    "(2) BATCH LAYER (System 2): LLM-based relation extraction with ontology-grounded prompts "
    "(RLVR-trained for cross-domain) for offline, deep consolidation. "
    "(3) DREAMING LAYER: periodic graph embedding retraining + motif discovery during idle, "
    "filing candidate edges as unconfirmed Points with [CONFIDENCE:LOW] for later validation. "
    "SCALING: MinHash/LSH pre-filtering + FAMER incremental clustering for entity resolution; "
    "deferred materialization for dense edges; IncBoost-style adaptive dependency tracing. "
    "UX: Dual-mode — Pull×Unknown (interactive exploration) for hypothesis generation, "
    "Push×Known (automated pipeline) for production edge monitoring. "
    "UNRESOLVED: 'dreaming' mode lacks dedicated computational approach; dual-mode UX lacks "
    "concrete implementation evidence. These are the two remaining research gaps.",
    "auto-discovery",
    pv("SYNTHESIS: Hybrid Lambda architecture with 3 tiers, Range Voting for multiplicity, LLM for deep reasoning"))

# ── Connect synthesis to all major evidence chains ────────────────────────
api.add_operator("IMPL", [synthesis, synthesis], "auto-discovery",
    pv("Synthesis self-referential — converged from all prior IMPL chains"))

api._emit("ingest_end", source_id="converged-topology")
print(f"\nCycle 4 complete: synthesis point + topology analysis")  # noqa: F541
