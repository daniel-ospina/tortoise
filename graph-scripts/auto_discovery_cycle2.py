"""Cycle 2 — Gap Analysis: query the graph for weak points."""
# Historical — uses embedded tortoise.db. Do not run against production Docker.
import sys, os  # noqa: E401, I001
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tortoise.api import EventAPI, provenance
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection

log = EventLog('auto-discovery-cycle2.jsonl')
proj = FalkorProjection()
api = EventAPI(log, initiated_by="user", agent_id="research-agent", projection=proj)

pv = lambda quote: provenance("auto-discovery", (0,0), quote, speaker="research-agent", extracted_by="manual@1.0")  # noqa: E731

api._emit("ingest_begin", source_id="gap-analysis", extractor_version="manual@1.0")

# 1. Find points involved in NAND contradictions
nand_points = proj.query(
    "MATCH (o:Point {op_type:'NAND'})-[:NAND]->(p:Point) "
    "RETURN o.content AS contradiction, o.id AS nand_id, collect(p.content) AS parties"
).result_set
print("=== NAND Contradictions ===")
for row in nand_points:
    print(f"  {row[0][:80]}...")
    for party in row[2]:
        print(f"    - {party[:80]}...")

# 2. Find points with NO incoming IMPL or NAND edges (low grounding)
orphans = proj.query(
    "MATCH (p:Point) WHERE p.is_operator = false "
    "OPTIONAL MATCH (p)<-[:IMPL]-(impl) "
    "OPTIONAL MATCH (p)<-[:NAND]-(nand) "
    "WITH p, count(DISTINCT impl) AS impl_count, count(DISTINCT nand) AS nand_count "
    "WHERE impl_count = 0 AND nand_count = 0 "
    "RETURN p.content AS content, p.id AS id, p.context AS context"
).result_set
print(f"\n=== Orphan Points (no edges): {len(orphans)} ===")
for row in orphans:
    print(f"  {row[0][:100]}...")

# 3. Find points with only NAND edges (seen only as contradictions, not supported)
only_nand = proj.query(
    "MATCH (p:Point) WHERE p.is_operator = false "
    "OPTIONAL MATCH (p)<-[:IMPL]-(impl) "
    "OPTIONAL MATCH (p)<-[:NAND]-(nand) "
    "WITH p, count(DISTINCT impl) AS impl_count, count(DISTINCT nand) AS nand_count "
    "WHERE impl_count = 0 AND nand_count > 0 "
    "RETURN p.content AS content, p.id AS id, nand_count"
).result_set
print(f"\n=== Points with NAND only (no IMPL support): {len(only_nand)} ===")
for row in only_nand:
    print(f"  [{row[2]} NANDs] {row[0][:100]}...")

# 4. Compute grounding scores
grounding = proj.compute_grounding(lam=0.6)
# Find lowest-grounded points
low_grounded = sorted(grounding.items(), key=lambda x: x[1])[:5]
print(f"\n=== Lowest Grounding Scores ===")  # noqa: F541
for pid, g in low_grounded:
    r = proj.query("MATCH (p:Point {id:$id}) RETURN p.content", params={"id": pid}).result_set
    content = r[0][0][:100] if r else "?"
    print(f"  {g:.4f} — {content}...")

# 5. Find UX-related contradictions
ux_nands = proj.query(
    "MATCH (o:Point {op_type:'NAND'})-[:NAND]->(p:Point) "
    "WHERE p.content CONTAINS 'UX' OR p.content CONTAINS 'interactive' OR p.content CONTAINS 'pipeline' "
    "OR p.content CONTAINS 'planning' OR p.content CONTAINS 'exploration' "
    "RETURN o.content AS contradiction, collect(p.content) AS parties"
).result_set
print(f"\n=== UX-Related NAND Contradictions: {len(ux_nands)} ===")
for row in ux_nands:
    print(f"  {row[0][:100]}...")

# ── File gap Points ──────────────────────────────────────────────────────

# GAP 1: Streaming vs batch contradiction is unresolved
g1 = api.add_point(
    "[CONFIDENCE:MEDIUM] GAP: Batch offline consolidation vs streaming real-time updates "
    "is an unresolved NAND contradiction with no bridging approach identified. "
    "No evidence of a system that successfully does both at production scale.",
    "auto-discovery", pv("GAP: batch vs streaming — no bridge found, contradiction unresolved"))

# GAP 2: No evidence for practical cross-domain embedding transfer
g2 = api.add_point(
    "[CONFIDENCE:LOW] GAP: Cross-domain entity mapping via embedding similarity is "
    "theoretically sound but has no IMPL-supporting evidence in this research. "
    "Only one point (p10) mentions it and no evidence points link to it.",
    "auto-discovery", pv("GAP: cross-domain embedding mapping has no supporting evidence chain"))

# GAP 3: Predictive multiplicity has no resolution mechanism
g3 = api.add_point(
    "[CONFIDENCE:MEDIUM] GAP: Predictive multiplicity (different embedding models → "
    "different predictions) is identified as a problem but no resolution mechanism "
    "(ensemble strategy, confidence weighting, model selection) is grounded in evidence.",
    "auto-discovery", pv("GAP: predictive multiplicity problem has no resolution mechanism"))

# GAP 4: UX dual-mode has no concrete implementation evidence
g4 = api.add_point(
    "[CONFIDENCE:LOW] GAP: Dual-mode paradigm (conversational AI + interactive exploration) "
    "is mentioned as a concept but has no concrete implementation evidence. "
    "AGENTiGraph is cited but no performance/UX metrics provided.",
    "auto-discovery", pv("GAP: dual-mode UX has conceptual support but no implementation evidence"))

# GAP 5: Incremental + cross-domain scaling is unaddressed
g5 = api.add_point(
    "[CONFIDENCE:MEDIUM] GAP: Incremental scaling research focuses on single-domain KGs. "
    "Cross-domain edge discovery compounds the scaling challenge (entity resolution across "
    "heterogeneous ontologies) but no research addresses this combination explicitly.",
    "auto-discovery", pv("GAP: incremental scaling + cross-domain combination is unresearched"))

# GAP 6: No computational approach has direct evidence for "dreaming" mode
g6 = api.add_point(
    "[CONFIDENCE:LOW] GAP: The 'dreaming' analog (offline consolidation through idle "
    "retraining, motif discovery) has no dedicated computational approach identified. "
    "It's inferred from offline consolidation but not explicitly researched.",
    "auto-discovery", pv("GAP: 'dreaming' mode has no dedicated computational approach"))

# ── Connect gaps to their root findings ───────────────────────────────────
api.add_operator("IMPL", [g1, g2], "auto-discovery",
    pv("Batch/streaming contradiction implies cross-domain mapping may be bottleneck"))

api.add_operator("IMPL", [g3, g2], "auto-discovery",
    pv("Predictive multiplicity compounds cross-domain mapping instability"))

api.add_operator("IMPL", [g5, g1], "auto-discovery",
    pv("Cross-domain scaling compounds the batch/streaming contradiction"))

api.add_operator("IMPL", [g4, g6], "auto-discovery",
    pv("Dual-mode UX gap relates to 'dreaming' mode — both lack concrete evidence"))

api._emit("ingest_end", source_id="gap-analysis")
print(f"\nCycle 2 complete: 6 gap points + 4 IMPL edges filed")  # noqa: F541
