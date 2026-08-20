"""Issue #6703 — Pros/cons of belief propagation approaches: BFS vs PageRank vs Embedding vs LLM."""
# Historical — uses embedded tortoise.db. Do not run against production Docker.
import sys, os  # noqa: E401, I001
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tortoise.api import EventAPI, provenance
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection

log = EventLog('bp-pros-cons.jsonl')
proj = FalkorProjection()
api = EventAPI(log, initiated_by="user", agent_id="research-agent", projection=proj)

pv = lambda quote: provenance("bp-pros-cons-research", (0,0), quote, speaker="research-agent", extracted_by="manual@1.0")  # noqa: E731
ctxt = "pros-cons"

api._emit("ingest_begin", source_id="bp-pros-cons-cycle5", extractor_version="manual@1.0")

# ════════════════════════════════════════════════════════════════════════════
# APPROACH 1: BFS Shock Propagation (Stream D — epistemic.py)
# ════════════════════════════════════════════════════════════════════════════

p1 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:BFS] BFS SHOCK PROPAGATION — OVERVIEW: "
    "Algorithm from epistemic.py (arXiv:2510.10042). Localized belief propagation "
    "using Breadth-First Search with max_depth=2, exponential damping (0.5), and "
    "subscription-based alerting. Only live claims recompute on shock events. "
    "O(affected subgraph) — not O(V+E). Designed for runtime response to single-claim "
    "changes rather than global equilibrium.",
    ctxt, pv("BFS shock propagation: localized, max_depth=2, damping=0.5, subscriptions"))

p2 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:BFS][DIM:COST] COMPUTATIONAL COST AT SCALE: "
    "O(|affected_subgraph|) per shock event — bounded by max_depth=2 and live-claim "
    "filtering (superseded claims are inert and not traversed). At depth 2 with average "
    "degree d, traversal touches O(d²) nodes. With d=10 edges/claim (typical for "
    "evidence graphs), ~100 nodes recomputed per shock. Each recompute is weighted "
    "edge aggregation — O(1) per node. Total: ~100 operations per shock. "
    "Scales linearly with edge density at bounded depth. Trivially parallelizable "
    "across independent shock events. Cost: essentially zero at all scales.",
    ctxt, pv("BFS cost: O(d²) per shock, ~100 ops, zero cost at all scales"))

p3 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:BFS][DIM:DATA] DATA REQUIREMENTS: "
    "Claims must exist with lifecycle states (draft/live/superseded). Evidence edges "
    "with typed relations (supports/contradicts) and confidence weights (0.0-1.0). "
    "Graph must be built before propagation can run. Subscriptions require threshold + "
    "callback definition. No external dependencies — pure in-memory Python. No embeddings, "
    "no LLM, no matrix operations. Data model is the EpistemicGraph adjacency list.",
    ctxt, pv("BFS data reqs: claims + edges + weights; no external deps"))

p4 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:BFS][DIM:INTERPRET] INTERPRETABILITY: "
    "Maximum interpretability. Every confidence change has an explicit audit trail: "
    "shock originates at epicenter, traverses BFS queue with depth marker, applies "
    "damping formula, and records old→new confidence per node. The damping formula "
    "(new = old*0.5 + raw*0.5) is visible and tunable. Edge weights directly surface "
    "which evidence drove a change. Subscriptions fire with threshold breach details. "
    "A human can trace: 'Claim #7 confidence dropped from 0.8 to 0.62 because NAND #9 "
    "propagated from depth 1 with weight 0.7 and damping halved it.'",
    ctxt, pv("BFS interpretability: max — explicit audit trail, visible formula, traceable path"))

p5 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:BFS][DIM:READINESS] OPERATIONAL READINESS: "
    "EXISTS TODAY. epistemic.py is implemented (~150 lines), used in E016 experiment "
    "harnesses, and tested with from_graphiti_edges() constructor. Integrates with "
    "Graphiti search results. No API dependencies. No infrastructure beyond Python dicts. "
    "Limitations: in-memory only (no persistence), no parallel shock handling, "
    "subscriptions are stubs (Phase 3 placeholder). Edge construction from raw text "
    "requires separate extraction pipeline (LLMExtractor or manual).",
    ctxt, pv("BFS readiness: EXISTS — epistemic.py implemented, tested in E016"))

p6 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:BFS][DIM:FAILURE] FAILURE MODES: "
    "(1) max_depth=2 hard cap: distant claims never propagate — if C→B→A with "
    "shock at C, A never receives it. (2) Damping formula is heuristic, not principled "
    "— 0.5 is arbitrary, no theoretical convergence guarantee on loopy graphs. "
    "(3) Linear confidence computation (w_supports / total) loses complex interactions "
    "— two weak supports ≠ one strong one, but treated identically. (4) No cycle "
    "handling beyond visited set — oscillations possible on revisits from different paths. "
    "(5) All-or-nothing confidence: 0.5 default for un-evidenced claims assumes "
    "uniform prior, not a learned prior. (6) Subscriptions are threshold-only — no "
    "trend detection, no rate-of-change alerts.",
    ctxt, pv("BFS failures: depth cap, heuristic damping, linear loss, cycle oscillation, uniform prior"))

# ════════════════════════════════════════════════════════════════════════════
# APPROACH 2: PageRank Grounding
# ════════════════════════════════════════════════════════════════════════════

p7 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:PAGERANK] PAGERANK GROUNDING — OVERVIEW: "
    "Global equilibrium approach g = (I - λM)^(-1) a. Stationary distribution over "
    "graph nodes representing normalized confidence scores. Requires resolution events "
    "to anchor the teleport vector a. Implemented via FalkorDB GraphBLAS PageRank "
    "(1.67s at million-node scale) or sparse power iteration (~50 iterations at α=0.85). "
    "Unlike BFS which responds to local shocks, PageRank computes the global picture.",
    ctxt, pv("PageRank grounding: global equilibrium, matrix inversion, resolution events"))

p8 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:PAGERANK][DIM:COST] COMPUTATIONAL COST AT SCALE: "
    "Direct inversion O(n³) — intractable above ~10⁶ nodes. Power iteration O(k·|E|): "
    "~50 iterations × |E| edges, linear in graph size. FalkorDB GraphBLAS: 1.67s for "
    "million-node graphs (sparse matrix backend). Memory: O(|V|+|E|) for sparse "
    "representation. Re-computation cost: PageRank must re-run on any graph change — "
    "there's no localized update. At 10K nodes with hourly recompute: negligible ($0). "
    "At 100K+ nodes with per-minute recompute: FalkorDB needed. Key insight: cost is "
    "in recompute frequency, not per-run — BFS amortizes better for frequent small changes.",
    ctxt, pv("PageRank cost: O(k·|E|) per run, 1.67s @ 1M nodes, must re-run on any change"))

p9 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:PAGERANK][DIM:DATA] DATA REQUIREMENTS: "
    "Complete graph with all nodes and edges. Teleport vector a must be defined — this "
    "is where resolution events anchor the system: resolved-YES claims get higher "
    "teleport weight. Damping factor λ (or α=1-λ) must be tuned (0.85 standard, 0.6 "
    "converges faster). All edges must exist before computation — no incremental update. "
    "FalkorDB requires the graph to be stored in its format. Stationary distribution "
    "assumption: graph structure is static between recomputes.",
    ctxt, pv("PageRank data reqs: complete graph, teleport vector, FalkorDB or sparse solver"))

p10 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:PAGERANK][DIM:INTERPRET] INTERPRETABILITY: "
    "High at the conceptual level — random-walk semantics are intuitive: 'confidence "
    "flows through evidence edges proportionally, high-confidence claims pass more "
    "weight.' Stationary distribution has clear meaning: proportion of time a random "
    "surfer spends at each claim. But scalar score limitation is critical: a single "
    "number cannot encode multi-dimensional confidence (separate confidence in "
    "factual accuracy vs relevance vs timeliness). Cannot distinguish 'well-evidenced "
    "but contested' from 'weakly evidenced but uncontested' — both produce similar "
    "scores. Gaming vectors: known from web search (link farms, reciprocal linking).",
    ctxt, pv("PageRank interpretability: high conceptually, limited by scalar scores, gameable"))

p11 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:PAGERANK][DIM:READINESS] OPERATIONAL READINESS: "
    "FalkorDB exists and is proven at scale (ArcadeDB benchmarks). PageRank is "
    "FalkorDB-native. But NOT yet integrated into Tortoise — current system uses BFS "
    "only. Migration requires: (1) wiring Tortoise graph to FalkorDB format, "
    "(2) defining teleport vector from resolution events, (3) scheduling recompute "
    "triggers, (4) replacing confidence computation with PageRank scores. P0 milestone "
    "from cycle 4 (2-4 hrs estimated). No API dependency — FalkorDB runs locally.",
    ctxt, pv("PageRank readiness: FalkorDB exists, NOT integrated, P0 milestone 2-4hrs"))

p12 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:PAGERANK][DIM:FAILURE] FAILURE MODES: "
    "(1) Global recompute on any change — a single new claim triggers full PageRank "
    "re-run, making it expensive for high-frequency update systems. (2) Stationary "
    "distribution assumption breaks if graph changes faster than recompute cycle — "
    "confidence scores lag reality. (3) Teleport vector collapse: if all resolution "
    "events are pending, a degrades to uniform → PageRank reduces to degree centrality. "
    "(4) Scalar scores conflate orthogonal confidence dimensions. (5) Isolated "
    "components receive zero rank — disconnected claims vanish from confidence landscape. "
    "(6) Damping factor λ is a global knob with no per-edge semantics — can't express "
    "'this NAND is stronger than that NAND'. (7) Computed at equilibrium, not "
    "responding to B-evidence shocks — the mechanism from epistemic-leverage-spec.md "
    "requires shock propagation, not equilibrium.",
    ctxt, pv("PageRank failures: global recompute cost, staleness lag, uniform fallback, scalar collapse"))

# ════════════════════════════════════════════════════════════════════════════
# APPROACH 3: Embedding Similarity
# ════════════════════════════════════════════════════════════════════════════

p13 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:EMBEDDING] EMBEDDING SIMILARITY — OVERVIEW: "
    "Candidate generation using vector similarity (cosine/dot product) between claim "
    "embeddings. Claims with high similarity are candidate edges for operator detection. "
    "Uses all-MiniLM-L6-v2 (384-dim, Apache-2.0, local). ANN indexing (FAISS) for "
    "sparse top-k retrieval. 88% precision at classification, adequate for candidate "
    "generation where downstream verification handles false positives. Zero API cost.",
    ctxt, pv("Embedding similarity: candidate generation, all-MiniLM-L6-v2, 88% precision, $0"))

p14 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:EMBEDDING][DIM:COST] COMPUTATIONAL COST AT SCALE: "
    "Model inference: $0 (local, Apache-2.0). ANN index build: O(n·d) one-time. "
    "Per-query retrieval: O(log n) with FAISS IVF index. Memory: 384-dim × 4 bytes "
    "× n nodes + index overhead. At 10K nodes with top-50 edges: ~160MB total. "
    "At 1M nodes: ~1.5GB + index. Dense O(n²) all-pairs avoided by ANN + sparse top-k. "
    "Key cost: embedding recompute when claim text changes — but claim text changes "
    "rarely (claims are extracted facts). Full re-index needed on new model.",
    ctxt, pv("Embedding cost: $0 local, O(log n) retrieval, 160MB @ 10K, 1.5GB @ 1M"))

p15 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:EMBEDDING][DIM:DATA] DATA REQUIREMENTS: "
    "Claim text must exist (not just UUID + confidence — actual natural language). "
    "Pre-trained model must be downloaded (all-MiniLM-L6-v2, ~90MB). FAISS or similar "
    "ANN library. Embeddings must be computed once and stored. Text quality matters: "
    "short/ambiguous claims produce weak embeddings. Language: English-only for "
    "all-MiniLM-L6-v2; multilingual needs different model. No labels needed for "
    "candidate generation — unsupervised.",
    ctxt, pv("Embedding data reqs: claim text, model download, FAISS, English-only"))

p16 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:EMBEDDING][DIM:INTERPRET] INTERPRETABILITY: "
    "Low. Vector distance (cosine similarity of 0.87) does not explain WHY two claims "
    "are similar — it's a geometric measure, not a semantic explanation. A human sees "
    "'Claim A and Claim B are 0.87 similar' with no insight into which dimensions drove "
    "the score. Cosine similarity is norm-agnostic: two claims can have high similarity "
    "by both being near-zero (degenerate). Dot product favors high-norm embeddings "
    "(frequent patterns). Saliency maps exist for transformer embeddings but require "
    "running the full model backward — expensive for bulk retrieval. Cannot distinguish "
    "similarity type: 'X supports Y' vs 'X contradicts Y' produce the same cosine score.",
    ctxt, pv("Embedding interpretability: low — geometric score, no semantic explanation, type-blind"))

p17 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:EMBEDDING][DIM:READINESS] OPERATIONAL READINESS: "
    "Model is downloadable and proven in benchmarks. E017/E018 experiments validate "
    "the embedding pre-filter + LLM tiered architecture. But NOT yet integrated into "
    "Tortoise — P1 milestone from cycle 4 (1 day estimated). Requires: (1) sentence-"
    "transformers dependency, (2) embedding computation pipeline, (3) FAISS index "
    "build + query, (4) wiring to LLMExtractor for tiered routing. No API dependency. "
    "Runs on CPU. M1 Mac (8GB) handles 10K nodes comfortably.",
    ctxt, pv("Embedding readiness: model exists, NOT integrated, P1 milestone 1 day"))

p18 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:EMBEDDING][DIM:FAILURE] FAILURE MODES: "
    "(1) 88% precision = 12% false positives — noise enters the graph. At 4K claims "
    "with all-pairs (~16M pairs), 12% FP = ~1.9M bogus candidate edges. Sparse top-k "
    "mitigates but doesn't eliminate. (2) Intra-similarity clustering: all top candidates "
    "from one topic, missing cross-domain connections. (3) Cold-start for novel claim "
    "types — model was trained on general text, not legal/medical/epistemic claims. "
    "(4) Norm sensitivity: short claims get small norms → penalized in dot product. "
    "(5) TYPE BLINDNESS: cannot distinguish supports from contradicts. 'Wetlands "
    "are habitat' and 'Jobs are temporary' may have high similarity (both about "
    "environmental impact) but represent opposing operator types. (6) Embedding "
    "distance ≠ semantic relevance for logical operators like NAND — the relationship "
    "is structural, not semantic. (7) Model staleness: frozen model never improves "
    "from system feedback.",
    ctxt, pv("Embedding failures: 12% FP noise, topic clustering, cold-start, type blindness, NAND blindness"))

# ════════════════════════════════════════════════════════════════════════════
# APPROACH 4: LLM Edge Discovery
# ════════════════════════════════════════════════════════════════════════════

p19 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:LLM] LLM EDGE DISCOVERY — OVERVIEW: "
    "Semantic classification of claim pairs into relation types (SUPPORTS/CONTRADICTS/"
    "NAND/NEUTRAL) using language models. Proven in E017/E018: flash + P2-definition "
    "prompt achieves F1=0.979 at $0.00008/run, 5.6s. Tiered architecture: embedding "
    "pre-filter → flash bulk classification → optional v4-pro verification on edge cases "
    "($0.0003 total per ambiguous claim). Can classify operator types that embeddings "
    "cannot: NAND, temporal precedence, causal. Production-ready at controlled cost.",
    ctxt, pv("LLM edge discovery: flash F1=0.979, $0.00008/run, tiered with embedding pre-filter"))

p20 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:LLM][DIM:COST] COMPUTATIONAL COST AT SCALE: "
    "Per-edge: $0.00005 (GPT-4.1 Nano) to $0.0005 (GPT-4.1). With embedding pre-filter "
    "(2.4% of all pairs pass filter): 4K claims × 4K × 0.024 × $0.00008 = $30/mo for "
    "daily reclassification. At 10K claims: $190/mo. At 40K claims: $3,000/mo — "
    "exceeds Premium tier. API latency: 3-6s per batch. Key optimization: classify only "
    "CHANGED edges, not all pairs — incremental cost ~2% of full reclassify. Embedding "
    "pre-filter reduces LLM calls by 97.6%. Without pre-filter: 4K² × $0.00008 = "
    "$1,280/mo (intractable). With pre-filter: $30/mo (viable).",
    ctxt, pv("LLM cost: $0.00005-0.0005/edge, $30/mo @ 4K with pre-filter, $3K @ 40K"))

p21 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:LLM][DIM:DATA] DATA REQUIREMENTS: "
    "Claim text (natural language, not just structured fields). Prompt template "
    "(P2-definition proven optimal). API key for LLM provider (OpenRouter/DeepSeek). "
    "Optional: v4-pro for verification on edge cases. Embedding pre-filter requires "
    "embeddings to exist first (dependency on Approach 3). Batch processing infrastructure "
    "for cost management. Rate limiting to prevent API cost overruns. System prompt "
    "must define claim types and operator taxonomy. No training data needed — few-shot "
    "or zero-shot classification.",
    ctxt, pv("LLM data reqs: claim text, prompt, API key, embedding pre-filter dependency"))

p22 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:LLM][DIM:INTERPRET] INTERPRETABILITY: "
    "Medium. LLM can output reasoning alongside classification: 'Claim A CONTRADICTS "
    "Claim B because A asserts wetland protection is adequate while B documents EPA "
    "violations for improper wetland mitigation.' This is inherently more interpretable "
    "than embedding similarity. But LLM reasoning is post-hoc rationalization — the "
    "model may fabricate plausible-sounding explanations for wrong classifications. "
    "Known failure: LLMs conflate correlation with causation, produce confident "
    "explanations for hallucinated relations. Non-deterministic: same input may produce "
    "different classifications across runs (temperature > 0). E018 showed reviewer/debate "
    "strategies DEGRADE quality for binary classification.",
    ctxt, pv("LLM interpretability: medium — can explain but rationalizes, non-deterministic"))

p23 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:LLM][DIM:READINESS] OPERATIONAL READINESS: "
    "Proven in E017/E018 experiments with 72-run factorial design. P2-definition prompt "
    "+ flash achieves production-quality F1=0.979. BUT: (1) Not yet integrated into "
    "Tortoise LLMExtractor — P3 milestone from cycle 4 (3-5 days). (2) Requires API "
    "dependency (OpenRouter or direct DeepSeek). (3) Needs cost monitoring and rate "
    "limiting infrastructure. (4) Embedding pre-filter must be built first (P1 milestone "
    "dependency). (5) Prompt engineering is fragile — E018 showed domain examples leak "
    "and inflate results. The core classification works today; the integration is the gap.",
    ctxt, pv("LLM readiness: proven in E017/E018, NOT integrated, P3 milestone 3-5 days, API dependency"))

p24 = api.add_point(
    "[CONFIDENCE:HIGH][APPROACH:LLM][DIM:FAILURE] FAILURE MODES: "
    "(1) COST EXPLOSION without strict pre-filter and rate limiting — a bug that "
    "removes the embedding gate triggers $1,280/mo at 4K claims. (2) API downtime: "
    "no offline fallback — graph edges go stale. (3) Prompt leakage: domain-specific "
    "examples in prompts contaminate results (E018: P6-domain gave false F1=1.000). "
    "(4) Hallucinated relations: LLM confidently invents connections between unrelated "
    "claims. (5) Non-determinism: same claim pair gets different classification across "
    "runs — undermines reproducibility. (6) Annotation ambiguity: same error all models "
    "made in E017 (utterance #26: prescription vs claim) — LLM inherits annotator bias. "
    "(7) Latency compounding: 3-6s per batch × thousands of pairs = minutes of wall-clock "
    "time. (8) Model deprecation: provider sunsets model version, requiring prompt "
    "re-validation. (9) Multi-agent degradation: E018 showed reviewer and debate strategies "
    "REDUCE F1 from 0.979 to 0.958 — more LLM calls ≠ better results.",
    ctxt, pv("LLM failures: cost explosion, API downtime, prompt leakage, hallucination, non-determinism, latency"))

# ════════════════════════════════════════════════════════════════════════════
# COMPARATIVE ANALYSIS — Cross-cutting findings
# ════════════════════════════════════════════════════════════════════════════

p25 = api.add_point(
    "[CONFIDENCE:HIGH][COMPARATIVE] COST COMPARISON (monthly @ 4K claims, daily recompute): "
    "BFS: $0 (in-memory, no infrastructure). PageRank: $0 (FalkorDB local, 1.67s/run). "
    "Embedding: $0 (local model, 160MB RAM). LLM: $30/mo (embedding pre-filter + flash, "
    "2.4% of pairs). LLM (no filter): $1,280/mo (intractable). COMBINED TIERED: $0 "
    "(BFS + PageRank + Embedding) + $30/mo (LLM on filtered pairs) = $30/mo total. "
    "This is the cycle-4 converged architecture cost baseline.",
    ctxt, pv("Cost comparison: BFS/PageRank/Embedding $0, LLM $30/mo, combined $30/mo"))

p26 = api.add_point(
    "[CONFIDENCE:HIGH][COMPARATIVE] DATA REQUIREMENT COMPARISON: "
    "BFS: claims + evidence edges + lifecycle states (all must exist). PageRank: complete "
    "graph + teleport vector from resolution events (resolution must exist). Embedding: "
    "claim text + pre-trained model (model must exist, text must be natural language). "
    "LLM: claim text + prompt + API key + embedding pre-filter (all previous layers "
    "must exist). DEPENDENCY CHAIN: BFS depends on edges. PageRank depends on resolution. "
    "Embedding depends on text. LLM depends on embedding pre-filter. Each approach has "
    "a different prerequisite that must exist before it produces value.",
    ctxt, pv("Data dependency chain: BFS←edges, PageRank←resolution, Embedding←text, LLM←embedding pre-filter"))

p27 = api.add_point(
    "[CONFIDENCE:HIGH][COMPARATIVE] INTERPRETABILITY RANKING (high→low): "
    "1. BFS (explicit audit trail, visible damping formula, traceable propagation path). "
    "2. PageRank (random-walk semantics intuitive, but scalar scores collapse dimensions). "
    "3. LLM (can explain reasoning but rationalizes, non-deterministic, hallucinates). "
    "4. Embedding (geometric score, no semantic explanation, type-blind, norm-sensitive). "
    "KEY TENSION: BFS is most interpretable but least powerful (bounded depth, no edge "
    "discovery). LLM is most powerful (discovers new edges, classifies types) but least "
    "reliable (non-deterministic, hallucinates). This is the interpretability-power "
    "trade-off at the heart of the tiered architecture.",
    ctxt, pv("Interpretability ranking: BFS > PageRank > LLM > Embedding. Trade-off with power."))

p28 = api.add_point(
    "[CONFIDENCE:HIGH][COMPARATIVE] OPERATIONAL READINESS RANKING (ready→needs work): "
    "1. BFS — EXISTS (epistemic.py, tested in E016). 2. Embedding — MODEL EXISTS "
    "(downloadable, proven) but NOT integrated into Tortoise. 3. PageRank — FALKORDB "
    "EXISTS but NOT integrated into Tortoise. 4. LLM — PROVEN in E017/E018 but NOT "
    "integrated into Tortoise, requires embedding pre-filter first. ALL FOUR approaches "
    "have production-ready implementations that exist today; the gap is INTEGRATION into "
    "Tortoise's extraction→propagation→query pipeline. BFS is the only one already wired.",
    ctxt, pv("Readiness: BFS integrated; Embedding/PageRank/LLM have implementations, need Tortoise wiring"))

p29 = api.add_point(
    "[CONFIDENCE:HIGH][COMPARATIVE] FAILURE MODE COMPARISON — WHAT BREAKS EACH: "
    "BFS breaks on: deep graphs (depth cap), loopy graphs (oscillation), uniform priors "
    "(0.5 default). PageRank breaks on: rapid graph changes (staleness), missing "
    "resolutions (uniform fallback), isolated components (zero rank). Embedding breaks "
    "on: NAND detection (type-blind), novel domains (cold-start), short text (norm "
    "penalty). LLM breaks on: cost overruns (missing pre-filter), API downtime (no "
    "fallback), prompt contamination (inflated results). COMMON FAILURE: all four "
    "approaches degrade when claims are poorly extracted — garbage claims → garbage "
    "propagation regardless of method. The extraction pipeline is the shared dependency.",
    ctxt, pv("Common failure: all degrade with poor extraction. Unique failures per approach."))

p30 = api.add_point(
    "[CONFIDENCE:HIGH][COMPARATIVE] STARTING POINT VS END STATE: "
    "STARTING POINT (today): BFS shock propagation is the only operational approach. "
    "It handles local confidence updates from new evidence. Works at all scales with zero "
    "cost. Limitation: cannot discover new edges, only propagates through existing ones. "
    "END STATE (tiered architecture): BFS for real-time shock response + PageRank for "
    "daily global equilibrium (dreaming consolidation) + Embedding for candidate edge "
    "generation ($0) + LLM for semantic edge classification on filtered pairs ($30/mo). "
    "Each approach fills a gap the others cannot: BFS=latency, PageRank=equilibrium, "
    "Embedding=recall, LLM=precision. The tiered architecture is not 'pick one' — it's "
    "'all four, at different timescales and cost tiers.'",
    ctxt, pv("Start→End: BFS today, tiered (BFS+PageRank+Embedding+LLM) as end state"))

# ════════════════════════════════════════════════════════════════════════════
# TENSIONS & NAND EDGES — Contradictions between approaches
# ════════════════════════════════════════════════════════════════════════════

p31 = api.add_point(
    "[CONFIDENCE:HIGH][TENSION] BFS vs PAGERANK: SHOCK RESPONSE vs EQUILIBRIUM. "
    "BFS propagates changes locally and immediately — a new NAND edge drops the affected "
    "claim's confidence within microseconds. PageRank requires full recompute to reflect "
    "the same change — minutes to hours depending on graph size and scheduling. These are "
    "fundamentally different operational models: BFS is event-driven (shock→response), "
    "PageRank is time-driven (schedule→recompute). They produce DIFFERENT confidence "
    "values for the same graph state because BFS has depth cap (local only) while "
    "PageRank has global equilibrium (all nodes influence all others through the stationary "
    "distribution). This is a tension, not a contradiction — they serve different purposes.",
    ctxt, pv("BFS vs PageRank tension: local shock vs global equilibrium, different confidence values"))

p32 = api.add_point(
    "[CONFIDENCE:HIGH][TENSION] EMBEDDING vs LLM: RECALL vs PRECISION. "
    "Embedding similarity achieves 88% precision — catches most true edges but with 12% "
    "false positives. LLM achieves 97.9% F1 — much higher precision but 30-375× more "
    "expensive per edge. Embedding is the RECALL engine (don't miss connections); LLM "
    "is the PRECISION engine (don't add noise). Together they form the tiered filter: "
    "embedding generates candidates (high recall, low cost) → LLM verifies (high precision, "
    "controlled cost). Separately, each is insufficient: embedding alone adds noise; LLM "
    "alone costs too much. The tension is resolved by the architecture.",
    ctxt, pv("Embedding vs LLM tension: recall engine vs precision engine, resolved by tiered architecture"))

p33 = api.add_point(
    "[CONFIDENCE:HIGH][TENSION] LLM INTERPRETABILITY PARADOX. "
    "LLM can EXPLAIN its edge classification ('X supports Y because...') but the "
    "explanation may be fabricated. BFS can't explain but its confidence changes are "
    "deterministic and reproducible — a human can verify the math. This creates a paradox: "
    "the approach that SEEMS most interpretable (LLM with natural language explanations) "
    "is actually less trustworthy than the approach that SEEMS opaque (BFS with numeric "
    "damping). Trust requires reproducibility, not eloquence. This is why LLM edges should "
    "carry confidence metadata and be flagged as 'LLM-classified' vs 'deterministic'.",
    ctxt, pv("LLM interpretability paradox: eloquent explanations from non-deterministic system"))

p34 = api.add_point(
    "[CONFIDENCE:HIGH][TENSION] EXTRACTION DEPENDENCY — SHARED BOTTLENECK. "
    "All four approaches depend on claim extraction quality. If LLMExtractor produces "
    "garbage claims (E016 showed this: pre-extracted propositions tested the wrong thing), "
    "then BFS propagates garbage, PageRank ranks garbage, embeddings match garbage to "
    "garbage, and LLM classifies garbage relations. The extraction pipeline is the "
    "single-point-of-failure for the entire propagation system. E017/E018 optimized "
    "extraction to F1=0.979 at $0.00008/run — this is the critical path, not propagation.",
    ctxt, pv("Extraction dependency: all four depend on claim quality — extraction is the bottleneck"))

# ════════════════════════════════════════════════════════════════════════════
# IMPL edges — What implies what
# ════════════════════════════════════════════════════════════════════════════

api.add_operator("IMPL", [p1, p6], ctxt,
    pv("BFS propagation requires pre-existing edges; depth cap means distant shocks are invisible"))
api.add_operator("IMPL", [p7, p12], ctxt,
    pv("PageRank grounding requires resolution events; without them, reduces to degree centrality"))
api.add_operator("IMPL", [p13, p18], ctxt,
    pv("Embedding similarity cannot detect NAND operators — logical relations are structural, not semantic"))
api.add_operator("IMPL", [p19, p24], ctxt,
    pv("LLM edge discovery requires embedding pre-filter; without it, cost is intractable at scale"))
api.add_operator("IMPL", [p25, p30], ctxt,
    pv("Combined tiered approach resolves shock response (BFS) vs equilibrium (PageRank) tension"))
api.add_operator("IMPL", [p34, p3], ctxt,
    pv("Claim extraction quality (E017/E018 F1=0.979) is prerequisite for all propagation approaches"))

# NAND edges — tensions that must be managed
api.add_operator("NAND", [p31, p32], ctxt,
    pv("BFS local confidence ≠ PageRank global confidence — same graph state produces different values"))
api.add_operator("NAND", [p33, p22], ctxt,
    pv("LLM explanations are post-hoc rationalizations, not deterministic audit trails"))
api.add_operator("NAND", [p28, p23], ctxt,
    pv("BFS integrated today but least powerful; LLM most powerful but not integrated — readiness ≠ capability"))

api._emit("ingest_end", source_id="bp-pros-cons-cycle5")

print(f"Log: bp-pros-cons.jsonl → tortoise.db")  # noqa: F541
