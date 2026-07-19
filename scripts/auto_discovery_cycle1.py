"""Cycle 1 — Research: Gather data on automated cross-domain connection discovery."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tortoise.api import EventAPI, provenance
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection

log = EventLog('auto-discovery-cycle1.jsonl')
proj = FalkorProjection('tortoise.db')
api = EventAPI(log, initiated_by="user", agent_id="research-agent", projection=proj)

pv = lambda quote: provenance("auto-discovery", (0,0), quote, speaker="research-agent", extracted_by="manual@1.0")

api._emit("ingest_begin", source_id="auto-discovery-research", extractor_version="manual@1.0")

# ── Topic 1: Computational Approaches for Edge Discovery ──────────────────
p1 = api.add_point(
    "[CONFIDENCE:HIGH] Three execution modes for KG edge discovery: "
    "offline consolidation (LLM-based multi-stage aggregation, batch), "
    "query-time inference (pull-based link prediction via GNNs/embeddings), "
    "background ingestion (continuous streaming, event-driven). Not simple cron.",
    "auto-discovery", pv("Three execution modes dominate: offline consolidation, query-time inference, background ingestion"))

p2 = api.add_point(
    "[CONFIDENCE:HIGH] System 1 (fast/cheap) analog: embedding-based link prediction "
    "(TransE, RotatE, ComplEx) provides fast similarity scoring for candidate edges. "
    "System 2 (slow/thorough) analog: LLM-based multi-stage extraction (KGGen, GraphRAG) "
    "with iterative dedup and consolidation. Dreaming analog: offline graph embedding "
    "retraining and motif discovery during idle periods.",
    "auto-discovery", pv("System 1 = embedding similarity, System 2 = LLM extraction, Dreaming = offline retraining"))

p3 = api.add_point(
    "[CONFIDENCE:MEDIUM] Push-based (ingest-time) discovery triggers immediate extraction "
    "when new text chunks arrive (e.g., GROBID chunking). Pull-based (query-time) waits "
    "for user query to trigger edge prediction. Hybrid: background ingestion with "
    "deferred materialization at query time.",
    "auto-discovery", pv("Push = ingest-time, Pull = query-time, Hybrid = background + deferred"))

# ── Topic 2: Graph Embedding Similarity for Link Prediction ───────────────
p4 = api.add_point(
    "[CONFIDENCE:HIGH] Translation-based models (TransE, RotatE) represent relations as "
    "vector operations: h + r ≈ t. Scoring: similarity(h+r, t). RotatE models relations "
    "as rotations in complex plane, handling asymmetry better than TransE.",
    "auto-discovery", pv("Translation models: h+r≈t, RotatE handles asymmetry via complex rotations"))

p5 = api.add_point(
    "[CONFIDENCE:HIGH] GNN-based models (R-GAT, RAGAT) use attention mechanisms across "
    "multi-hop neighborhoods for richer embeddings. 2024 trend: extended relational GAT "
    "with dynamic attention weighting across geometric criteria.",
    "auto-discovery", pv("R-GAT: attention across multi-hop neighborhoods, 2024 trend"))

p6 = api.add_point(
    "[CONFIDENCE:HIGH] Predictive multiplicity: different embedding models trained on "
    "same data yield varying link predictions. This is a fundamental stability challenge — "
    "no single embedding model is authoritative across all relation types.",
    "auto-discovery", pv("Predictive multiplicity: different models → different predictions on same data"))

# ── Topic 3: LLM-Based Relation Extraction Cross-Domain ──────────────────
p7 = api.add_point(
    "[CONFIDENCE:HIGH] RLVR (R1-RE, 2025): Reinforcement learning with verifiable rewards "
    "aligns LLM reasoning with human multi-step annotation workflows. Achieves substantial "
    "out-of-domain improvements by strengthening chain-of-thought for relation extraction.",
    "auto-discovery", pv("R1-RE: RLVR aligns LLM reasoning to human workflow for cross-domain RE"))

p8 = api.add_point(
    "[CONFIDENCE:HIGH] Ontology-grounded extraction: two-step — (1) generate domain-specific "
    "ontology from text, (2) use as directive prompt for RDF triple extraction. Enables "
    "schema evolution across domains via iterative prompt tailoring.",
    "auto-discovery", pv("Ontology-grounded: generate ontology → use as extraction prompt"))

p9 = api.add_point(
    "[CONFIDENCE:MEDIUM] Hybrid end-to-end KG construction (AutoSchemaKG, 2025): "
    "LLM-driven ontology induction via unsupervised clustering, followed by scalable "
    "rule-based information extraction. Combines schema-based consistency with "
    "schema-free adaptability.",
    "auto-discovery", pv("AutoSchemaKG: unsupervised clustering ontology + rule-based IE"))

p10 = api.add_point(
    "[CONFIDENCE:MEDIUM] Cross-domain entity mapping via embedding similarity + tf-idf "
    "reranking. Embedding proximity finds candidates, keyword alignment filters false "
    "positives. Effective for implicit cross-domain connections.",
    "auto-discovery", pv("Cross-domain mapping: embedding similarity + tf-idf reranking"))

# ── Topic 4: Incremental Scaling Challenges ────────────────────────────────
p11 = api.add_point(
    "[CONFIDENCE:HIGH] Expensive dependency tracing: edge deletion/update requires "
    "tracing all affected vertices — 70-80% of deletion cost. IncBoost solves this "
    "with adaptive dependency tracing that handles batches up to 60% of graph size.",
    "auto-discovery", pv("Dependency tracing = 70-80% deletion cost; IncBoost = adaptive fix"))

p12 = api.add_point(
    "[CONFIDENCE:HIGH] Quadratic entity consolidation: pairwise similarity for dedup "
    "is O(n²). Mitigation: MinHash/LSH before entity linkage to reduce candidate pairs. "
    "Without hashing, large datasets (16M+ entities) are impractical.",
    "auto-discovery", pv("Quadratic dedup: O(n²), mitigated by MinHash/LSH pre-filtering"))

p13 = api.add_point(
    "[CONFIDENCE:HIGH] Deferred edge discovery at query time: pre-computing dense edges "
    "causes quadratic growth (every new node links to all past nodes). Deferring to query "
    "time avoids this degradation. Trade-off: query latency vs ingestion throughput.",
    "auto-discovery", pv("Deferred discovery: query-time edges avoid quadratic materialization"))

p14 = api.add_point(
    "[CONFIDENCE:MEDIUM] Streaming architectures: moving from batch re-computation to "
    "streaming incremental updates handles dynamic data better. Continuous pipelines "
    "prevent unsustainable full re-computation when sources change.",
    "auto-discovery", pv("Streaming > batch for dynamic data; prevents full re-computation"))

# ── Topic 5: UX Modes for KG Interaction ──────────────────────────────────
p15 = api.add_point(
    "[CONFIDENCE:HIGH] Four-mode interaction matrix (Stanford CS520): Pull×Known "
    "(goal-oriented query), Pull×Unknown (interactive exploration), Push×Known "
    "(automated pipeline), Push×Unknown (attentive/reactive alerts).",
    "auto-discovery", pv("Stanford CS520: 4-mode matrix — Pull/Push × Known/Unknown questions"))

p16 = api.add_point(
    "[CONFIDENCE:HIGH] Interactive planning mode (Pull×Unknown): visual graph exploration, "
    "entity hopping, semantic navigation for organic discovery. Requires interactive "
    "visualizations, node-link diagrams, dynamic queries. AGENTiGraph Exploration Mode.",
    "auto-discovery", pv("Interactive planning: visual exploration, entity hopping, organic discovery"))

p17 = api.add_point(
    "[CONFIDENCE:HIGH] Automated pipeline mode (Push×Known): batch reports, dashboards, "
    "scheduled analytics for predefined business questions. Uses static reports, not "
    "interactive graphs. Value: pre-computed insights without manual traversal.",
    "auto-discovery", pv("Automated pipeline: batch reports, dashboards, pre-computed insights"))

p18 = api.add_point(
    "[CONFIDENCE:MEDIUM] Dual-mode paradigm (AGENTiGraph): combining conversational AI "
    "(intent interpretation) with interactive graph exploration (traversal). Bridges "
    "planning and pipeline needs. TRACE: visual reasoning paths for trust building.",
    "auto-discovery", pv("Dual-mode: conversational AI + interactive exploration bridges paradigms"))

# ── IMPL edges (implication: source supports/implies target) ──────────────
# Embedding similarity → enables pull-based discovery
api.add_operator("IMPL", [p4, p2], "auto-discovery",
    pv("Embedding similarity scoring enables fast System-1 pull-based edge discovery"))

# GNN attention → enables multi-hop link prediction
api.add_operator("IMPL", [p5, p4], "auto-discovery",
    pv("R-GAT attention mechanisms improve multi-hop link prediction quality"))

# LLM extraction → enables offline consolidation
api.add_operator("IMPL", [p7, p1], "auto-discovery",
    pv("LLM-based relation extraction is the primary mechanism for offline consolidation"))

# Ontology-grounded → improves cross-domain extraction
api.add_operator("IMPL", [p8, p7], "auto-discovery",
    pv("Ontology grounding provides structural priors that improve cross-domain RE accuracy"))

# MinHash/LSH → enables scalable consolidation
api.add_operator("IMPL", [p12, p11], "auto-discovery",
    pv("MinHash/LSH pre-filtering reduces candidate pairs, making dependency tracing scalable"))

# Deferred discovery → avoids quadratic materialization
api.add_operator("IMPL", [p13, p12], "auto-discovery",
    pv("Deferred edge discovery avoids quadratic growth in entity consolidation"))

# RLVR → enables cross-domain adaptation
api.add_operator("IMPL", [p7, p10], "auto-discovery",
    pv("RLVR strengthens multi-step reasoning, improving cross-domain entity mapping"))

# Embedding similarity + tf-idf → cross-domain mapping
api.add_operator("IMPL", [p4, p10], "auto-discovery",
    pv("Embedding similarity is the foundation for cross-domain entity mapping"))

# Predictive multiplicity → need for ensemble
api.add_operator("IMPL", [p6, p4], "auto-discovery",
    pv("Predictive multiplicity implies no single embedding model suffices; ensembles needed"))

# Dual-mode UX → bridges planning/pipeline
api.add_operator("IMPL", [p18, p16], "auto-discovery",
    pv("Dual-mode paradigm bridges automated pipeline with interactive exploration"))

# ── NAND edges (contradiction: these approaches conflict) ─────────────────
# Batch pipeline vs streaming real-time
api.add_operator("NAND", [p1, p14], "auto-discovery",
    pv("Batch offline consolidation conflicts with streaming real-time update requirements"))

# Schema-based vs schema-free extraction
api.add_operator("NAND", [p8, p9], "auto-discovery",
    pv("Schema-based (ontology-grounded) extraction has conflicting goals with schema-free (exploratory) extraction"))

# Materialization vs deferred discovery
api.add_operator("NAND", [p11, p13], "auto-discovery",
    pv("Pre-computing edges (materialization) trades off against deferred query-time discovery"))

# Interactive planning vs automated pipeline UX
api.add_operator("NAND", [p16, p17], "auto-discovery",
    pv("Interactive planning requires exploration UX; automated pipeline requires static reporting UX"))

# System 1 (fast embedding) vs System 2 (LLM) in same path
api.add_operator("NAND", [p2, p7], "auto-discovery",
    pv("System-1 embedding similarity and System-2 LLM extraction have different latency/cost profiles"))

api._emit("ingest_end", source_id="auto-discovery-research")
print(f"Cycle 1 complete: {18} points + {8} IMPL + {5} NAND edges filed")
