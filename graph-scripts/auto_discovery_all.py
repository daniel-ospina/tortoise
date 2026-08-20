"""Fixed: all 4 cycles in one script with none of the same bugs."""
# Historical — uses embedded tortoise.db. Do not run against production Docker.
import sys, os  # noqa: E401, I001
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tortoise.api import EventAPI, provenance
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection

# ── Cycle 1 ──────────────────────────────────────────────────────────────
log1 = EventLog('auto-discovery-cycle1.jsonl')
proj = FalkorProjection()
api = EventAPI(log1, initiated_by="user", agent_id="research-agent", projection=proj)
pv = lambda quote: provenance("auto-discovery", (0,0), quote, speaker="research-agent", extracted_by="manual@1.0")  # noqa: E731

api._emit("ingest_begin", source_id="auto-discovery-research", extractor_version="manual@1.0")

# Topic 1: Computational Approaches
p1 = api.add_point("[CONFIDENCE:HIGH] Three execution modes for KG edge discovery: offline consolidation (LLM-based multi-stage aggregation, batch), query-time inference (pull-based link prediction via GNNs/embeddings), background ingestion (continuous streaming, event-driven).", "auto-discovery", pv("Three execution modes dominate"))
p2 = api.add_point("[CONFIDENCE:HIGH] System 1 analog: embedding-based link prediction (TransE, RotatE, ComplEx). System 2 analog: LLM-based multi-stage extraction (KGGen, GraphRAG). Dreaming analog: offline retraining + motif discovery.", "auto-discovery", pv("System 1=embeddings, System 2=LLM, Dreaming=offline retraining"))
p3 = api.add_point("[CONFIDENCE:MEDIUM] Push-based (ingest-time) discovery triggers immediate extraction. Pull-based (query-time) waits for query. Hybrid: background ingestion with deferred materialization.", "auto-discovery", pv("Push=ingest-time, Pull=query-time, Hybrid=deferred"))

# Topic 2: Graph Embedding Similarity
p4 = api.add_point("[CONFIDENCE:HIGH] Translation-based models (TransE, RotatE): h+r≈t. RotatE models relations as rotations in complex plane, handling asymmetry better.", "auto-discovery", pv("Translation models: h+r≈t"))
p5 = api.add_point("[CONFIDENCE:HIGH] GNN-based models (R-GAT, RAGAT) use attention mechanisms across multi-hop neighborhoods. 2024 trend: extended relational GAT.", "auto-discovery", pv("R-GAT attention for multi-hop"))
p6 = api.add_point("[CONFIDENCE:HIGH] Predictive multiplicity: different embedding models trained on same data yield varying link predictions — no single authoritative model.", "auto-discovery", pv("Predictive multiplicity problem"))

# Topic 3: LLM-Based Cross-Domain RE
p7 = api.add_point("[CONFIDENCE:HIGH] RLVR (R1-RE, 2025): RL with verifiable rewards aligns LLM reasoning to human multi-step annotation for cross-domain RE.", "auto-discovery", pv("R1-RE: RLVR for cross-domain RE"))
p8 = api.add_point("[CONFIDENCE:HIGH] Ontology-grounded extraction: generate domain ontology from text, use as directive prompt for RDF triple extraction. Schema evolution via iterative prompting.", "auto-discovery", pv("Ontology-grounded: ontology→extraction prompt"))
p9 = api.add_point("[CONFIDENCE:MEDIUM] Hybrid end-to-end (AutoSchemaKG, 2025): unsupervised clustering ontology induction + rule-based IE. Schema-based consistency + schema-free adaptability.", "auto-discovery", pv("AutoSchemaKG: hybrid schema-based + schema-free"))
p10 = api.add_point("[CONFIDENCE:MEDIUM] Cross-domain entity mapping via embedding similarity + tf-idf reranking. Embedding proximity finds candidates, keyword alignment filters false positives.", "auto-discovery", pv("Cross-domain mapping: embeddings + tf-idf"))

# Topic 4: Incremental Scaling
p11 = api.add_point("[CONFIDENCE:HIGH] Expensive dependency tracing: 70-80% of edge deletion cost. IncBoost solves with adaptive tracing handling batches up to 60% of graph size.", "auto-discovery", pv("Dependency tracing=70-80% cost; IncBoost=adaptive"))
p12 = api.add_point("[CONFIDENCE:HIGH] Quadratic entity consolidation: O(n²) pairwise dedup. Mitigation: MinHash/LSH pre-filtering reduces candidate pairs.", "auto-discovery", pv("Quadratic dedup→MinHash/LSH mitigation"))
p13 = api.add_point("[CONFIDENCE:HIGH] Deferred edge discovery at query time avoids quadratic materialization. Trade-off: query latency vs ingestion throughput.", "auto-discovery", pv("Deferred discovery avoids quadratic growth"))
p14 = api.add_point("[CONFIDENCE:MEDIUM] Streaming architectures: move from batch re-computation to streaming incremental updates for dynamic data.", "auto-discovery", pv("Streaming > batch for dynamic data"))

# Topic 5: UX Modes
p15 = api.add_point("[CONFIDENCE:HIGH] Four-mode interaction matrix (Stanford CS520): Pull×Known (query), Pull×Unknown (exploration), Push×Known (pipeline), Push×Unknown (alerts).", "auto-discovery", pv("Stanford 4-mode matrix"))
p16 = api.add_point("[CONFIDENCE:HIGH] Interactive planning mode (Pull×Unknown): visual graph exploration, entity hopping, semantic navigation. AGENTiGraph Exploration Mode.", "auto-discovery", pv("Interactive planning: exploration UX"))
p17 = api.add_point("[CONFIDENCE:HIGH] Automated pipeline mode (Push×Known): batch reports, dashboards, scheduled analytics. Static reports, not interactive.", "auto-discovery", pv("Automated pipeline: batch reports"))
p18 = api.add_point("[CONFIDENCE:MEDIUM] Dual-mode paradigm (AGENTiGraph): conversational AI + interactive exploration bridges planning and pipeline needs.", "auto-discovery", pv("Dual-mode bridges paradigms"))

# IMPL edges
api.add_operator("IMPL", [p4, p2], "auto-discovery", pv("Embedding similarity enables System-1 pull-based discovery"))
api.add_operator("IMPL", [p5, p4], "auto-discovery", pv("R-GAT attention improves multi-hop link prediction"))
api.add_operator("IMPL", [p7, p1], "auto-discovery", pv("LLM extraction is primary mechanism for offline consolidation"))
api.add_operator("IMPL", [p8, p7], "auto-discovery", pv("Ontology grounding improves cross-domain RE accuracy"))
api.add_operator("IMPL", [p12, p11], "auto-discovery", pv("MinHash/LSH enables scalable dependency tracing"))
api.add_operator("IMPL", [p13, p12], "auto-discovery", pv("Deferred discovery avoids quadratic consolidation"))
api.add_operator("IMPL", [p7, p10], "auto-discovery", pv("RLVR strengthens cross-domain entity mapping"))
api.add_operator("IMPL", [p6, p4], "auto-discovery", pv("Predictive multiplicity→no single model suffices"))

# NAND edges
api.add_operator("NAND", [p1, p14], "auto-discovery", pv("Batch offline vs streaming real-time — conflicting latency"))
api.add_operator("NAND", [p8, p9], "auto-discovery", pv("Schema-based vs schema-free — conflicting paradigms"))
api.add_operator("NAND", [p11, p13], "auto-discovery", pv("Materialization vs deferred discovery — compute trade-off"))
api.add_operator("NAND", [p16, p17], "auto-discovery", pv("Interactive planning vs automated pipeline — conflicting UX"))
api.add_operator("NAND", [p2, p7], "auto-discovery", pv("System-1 embedding vs System-2 LLM — latency/cost trade-off"))

api._emit("ingest_end", source_id="auto-discovery-research")
print("Cycle 1: 18 points + 8 IMPL + 5 NAND")

# ── Cycle 2: Gap Analysis ───────────────────────────────────────────────
log2 = EventLog('auto-discovery-cycle2.jsonl')
api2 = EventAPI(log2, initiated_by="user", agent_id="research-agent", projection=proj)
api2._emit("ingest_begin", source_id="gap-analysis", extractor_version="manual@1.0")

g1 = api2.add_point("[CONFIDENCE:MEDIUM] GAP: Batch offline consolidation vs streaming real-time updates is an unresolved NAND contradiction with no bridging approach identified. No evidence of system doing both at production scale.", "auto-discovery", pv("GAP: batch vs streaming — no bridge"))
g2 = api2.add_point("[CONFIDENCE:LOW] GAP: Cross-domain entity mapping via embedding similarity is theoretically sound but has no IMPL-supporting evidence. Only one point mentions it with no evidence chain.", "auto-discovery", pv("GAP: cross-domain embedding mapping lacks evidence"))
g3 = api2.add_point("[CONFIDENCE:MEDIUM] GAP: Predictive multiplicity is identified as a problem but no resolution mechanism (ensemble, confidence weighting, model selection) is grounded in evidence.", "auto-discovery", pv("GAP: predictive multiplicity has no resolution"))
g4 = api2.add_point("[CONFIDENCE:LOW] GAP: Dual-mode paradigm (conversational AI + interactive exploration) is conceptual only — AGENTiGraph cited but no performance/UX metrics.", "auto-discovery", pv("GAP: dual-mode UX has no implementation evidence"))
g5 = api2.add_point("[CONFIDENCE:MEDIUM] GAP: Incremental scaling research focuses on single-domain KGs. Cross-domain edge discovery compounds scaling (entity resolution across heterogeneous ontologies) but no research addresses this combination.", "auto-discovery", pv("GAP: incremental + cross-domain scaling unresearched"))
g6 = api2.add_point("[CONFIDENCE:LOW] GAP: The 'dreaming' analog (offline consolidation through idle retraining, motif discovery) has no dedicated computational approach — inferred from offline consolidation but not explicitly researched.", "auto-discovery", pv("GAP: dreaming mode has no dedicated approach"))

api2.add_operator("IMPL", [g1, g2], "auto-discovery", pv("Batch/streaming contradiction→cross-domain mapping bottleneck"))
api2.add_operator("IMPL", [g3, g2], "auto-discovery", pv("Predictive multiplicity compounds cross-domain instability"))
api2.add_operator("IMPL", [g5, g1], "auto-discovery", pv("Cross-domain scaling compounds batch/streaming contradiction"))
api2.add_operator("IMPL", [g4, g6], "auto-discovery", pv("Dual-mode UX gap relates to dreaming mode — both lack evidence"))
api2._emit("ingest_end", source_id="gap-analysis")
print("Cycle 2: 6 gap points + 4 IMPL")

# ── Cycle 3: Validation ──────────────────────────────────────────────────
log3 = EventLog('auto-discovery-cycle3.jsonl')
api3 = EventAPI(log3, initiated_by="user", agent_id="research-agent", projection=proj)
api3._emit("ingest_begin", source_id="gap-validation", extractor_version="manual@1.0")

e1 = api3.add_point("[CONFIDENCE:HIGH] Hybrid Lambda/Kappa architecture resolves batch vs streaming: batch layer (cold path, hours/days), speed layer (hot path, <1s), serving layer (merges both). Well-established pattern.", "auto-discovery", pv("Hybrid Lambda resolves batch/streaming"))
e2 = api3.add_point("[CONFIDENCE:HIGH] Real-time entity resolution via lightweight projector networks + CDC keeps graph as mirror of reality. Temporal context tracking (Graphiti) maintains provenance.", "auto-discovery", pv("Real-time ER + CDC + temporal tracking"))
e3 = api3.add_point("[CONFIDENCE:HIGH] Range Voting (social choice theory) resolves predictive multiplicity: reduces conflicting predictions 66-78% while maintaining Hit@K. Outperforms Borda, majority, averaging.", "auto-discovery", pv("Range Voting reduces multiplicity 66-78%"))
e4 = api3.add_point("[CONFIDENCE:HIGH] Ensemble: (1) Range Voting for multiplicity, (2) Adaptive Weighting for noise, (3) Attention for per-query model selection.", "auto-discovery", pv("Two-layer ensemble for robust link prediction"))
e5 = api3.add_point("[CONFIDENCE:HIGH] FAMER (2024): incremental clustering + cluster repair, order-independent, batch-like quality for multi-source ER.", "auto-discovery", pv("FAMER: incremental clustering + repair"))
e6 = api3.add_point("[CONFIDENCE:HIGH] IncRML (2024): CDC-based declarative incremental KG construction. EAGER: first embedding-supported ER with multiple entity types, no schema matching needed.", "auto-discovery", pv("IncRML + EAGER for incremental + multi-type ER"))
e7 = api3.add_point("[CONFIDENCE:MEDIUM] Hybrid LLM+Rule pipeline: LLM for ontology induction + rule-based IE/ER. Config-only domain transfer. Embedding-based blocking avoids O(n²).", "auto-discovery", pv("Hybrid LLM+Rule: config-only domain transfer"))
e8 = api3.add_point("[CONFIDENCE:MEDIUM] Embedding-based blocking (ANN) + zero-shot LLM ER + pre-KG resolution. Cross-domain embedding transfer remains active research area.", "auto-discovery", pv("ANN + zero-shot ER + pre-KG resolution"))

# IMPL edges
api3.add_operator("IMPL", [e1, e2], "auto-discovery", pv("Lambda architecture uses real-time ER for speed layer"))
api3.add_operator("IMPL", [e2, e1], "auto-discovery", pv("CDC + temporal tracking feed hot path"))
api3.add_operator("IMPL", [e3, e4], "auto-discovery", pv("Range Voting is primary; ensemble wraps with weighting"))
api3.add_operator("IMPL", [e5, e6], "auto-discovery", pv("FAMER + IncRML together for incremental multi-source ER"))
api3.add_operator("IMPL", [e6, e7], "auto-discovery", pv("EAGER embedding-based ER feeds hybrid LLM+Rule"))
api3.add_operator("IMPL", [e8, e7], "auto-discovery", pv("Embedding blocking enables cross-domain ER in hybrid pipelines"))

# Revise gap points — use direct approach with query+update
api3.revise_point(g1, new_content="[CONFIDENCE:LOW] GAP: Batch offline consolidation vs streaming real-time updates — RESOLVED by hybrid Lambda architecture (Cycle 3 evidence). Bridging approach identified.", corrects=None)
api3.revise_point(g2, new_content="[CONFIDENCE:MEDIUM] GAP: Cross-domain entity mapping via embedding similarity — PARTIALLY RESOLVED. ANN blocking + zero-shot LLM ER + pre-KG resolution exist but remain active research.", corrects=None)
api3.revise_point(g3, new_content="[CONFIDENCE:LOW] GAP: Predictive multiplicity — RESOLVED by Range Voting (66-78% reduction) + two-layer ensemble strategy (Cycle 3 evidence).", corrects=None)
api3.revise_point(g4, new_content="[CONFIDENCE:LOW] GAP: Dual-mode UX — UNRESOLVED. Still no concrete implementation evidence from Cycle 3.", corrects=None)
api3.revise_point(g5, new_content="[CONFIDENCE:LOW] GAP: Incremental + cross-domain scaling — RESOLVED. FAMER, IncRML, EAGER, hybrid LLM+Rule approaches exist (Cycle 3 evidence).", corrects=None)
api3.revise_point(g6, new_content="[CONFIDENCE:LOW] GAP: 'Dreaming' mode — UNRESOLVED. Still no dedicated computational approach found.", corrects=None)

api3._emit("ingest_end", source_id="gap-validation")
print("Cycle 3: 8 evidence points + 6 IMPL + 6 revisions")

# ── Cycle 4: Synthesis ───────────────────────────────────────────────────
log4 = EventLog('auto-discovery-cycle4.jsonl')
api4 = EventAPI(log4, initiated_by="user", agent_id="research-agent", projection=proj)
api4._emit("ingest_begin", source_id="converged-topology", extractor_version="manual@1.0")

synth = api4.add_point(
    "[CONFIDENCE:HIGH] CONVERGED RECOMMENDATION: Automated cross-domain connection discovery via HYBRID LAMBDA ARCHITECTURE. "
    "SPEED LAYER (System 1): embedding link prediction (RotatE/R-GAT + Range Voting ensemble) for real-time pull-based discovery. "
    "BATCH LAYER (System 2): LLM extraction (RLVR + ontology-grounded prompts) for offline deep consolidation. "
    "DREAMING LAYER: periodic retraining + motif discovery during idle, filing candidate edges as [CONFIDENCE:LOW]. "
    "SCALING: MinHash/LSH + FAMER incremental clustering + deferred materialization + IncBoost adaptive tracing. "
    "UX: Dual-mode — Pull×Unknown (exploration) + Push×Known (pipeline). "
    "UNRESOLVED: dreaming mode lacks dedicated approach; dual-mode UX lacks implementation evidence.",
    "auto-discovery", pv("SYNTHESIS: Hybrid Lambda, 3 tiers, Range Voting, LLM deep reasoning"))

# Connect synthesis to evidence
api4.add_operator("IMPL", [synth, e1], "auto-discovery", pv("Synthesis builds on hybrid Lambda architecture"))
api4.add_operator("IMPL", [synth, e3], "auto-discovery", pv("Synthesis uses Range Voting for multiplicity resolution"))
api4.add_operator("IMPL", [synth, e5], "auto-discovery", pv("Synthesis uses FAMER for incremental scaling"))

api4._emit("ingest_end", source_id="converged-topology")
print("Cycle 4: 1 synthesis point + 3 IMPL")

# ── Final topology stats ─────────────────────────────────────────────────
from tortoise.log import EventLog as EL  # noqa: E402, I001
all_events = []
for logfile in ['auto-discovery-cycle1.jsonl', 'auto-discovery-cycle2.jsonl',
                'auto-discovery-cycle3.jsonl', 'auto-discovery-cycle4.jsonl']:
    all_events.extend(EL(logfile).read_all())

from tortoise.projection import fold  # noqa: E402, I001
points = fold(all_events)
ad_points = {k: v for k, v in points.items() if v.get('context') == 'auto-discovery'}
stats = [p for p in ad_points.values() if p.get('operator')]
impl_count = sum(1 for p in stats if p['operator']['op_type'] == 'IMPL')
nand_count = sum(1 for p in stats if p['operator']['op_type'] == 'NAND')
research_count = len(ad_points) - len(stats)
print(f"\nFinal topology: {len(ad_points)} total ({research_count} research, {len(stats)} operators: {impl_count} IMPL + {nand_count} NAND)")
