# Scoping — #1657: Retrieval Levers (double-diamond scoping)

**Issue:** #1657 · **Tier:** standard · **Level:** project · **Complexity:** standard (Research, Architecture)
**Date:** 2026-08-22 · **Deliverables dir:** `/tmp/followup-docs/1657/` (research.md + this scoping.md)
**Repo read:** `/private/tmp/tw-1349` (post-#1349 branch; no files modified, no branches/commits)

---

## Phase 1 — problem-diverge: alternative framings

The issue frames the problem as "research 4 levers; decide which warrant an implementation issue." Alternative framings considered:

- **Framing A (original):** each lever is an independent candidate improvement, measured on the existing harness, go/no-go per lever.
- **Framing B (interaction-first):** the levers are *not independent* — L3 (fusion) and L4 (TF-IDF 4th leg) interact on the same fusion surface, and L1/L2 both modify the query vector/string that all legs see. Scope the *measurement protocol* as a joint sweep with an interaction matrix, not 4 independent experiments.
- **Framing C (root-cause):** the root cause is "recall coverage vs top-10 ordering are both below target; the vector leg is the strongest signal but diluted by equal-weight fusion, and the sparse leg is brittle on paraphrase." The levers are symptoms of two root causes: **fusion dilution** and **lexical brittleness**.
- **Framing D (reject-the-framing):** "is the embedder the wrong bottleneck again?" — the #1349 research showed LongMemEval leader Hindsight (91.4%) uses commodity embeddings; AutoMem: 54/65 residual errors had gold in top-5 → the bottleneck at high recall is the *reader*, not retrieval. But at our recall level (0.786 turn_recall@10) retrieval is still the binding constraint — reader-side saturation is not reached (AutoMem's 97% R@5 is far above us).

### Assumptions mapped
| Assumption | Status |
|---|---|
| The 300ms E2E budget excludes any per-query LLM call (HyDE/paraphrase generation in the hot path) | [validated] — #317 research: CE +210ms/top-10 CPU; the eval reader/judge are out-of-loop by design |
| bge-small is the fixed embedder for this scope (no re-embed event) | [validated] — #1349 closed, ADR-009 |
| The Docker/HNSW surface is the decision surface (embedded = CI-only) | [validated] — #1349 gate precedent |
| `search_keys` are reliably written per point by the current extractor | [unverified] — coverage by category not measured; E3 #1535 annotates but no consumer exists |
| Question-type labels (TR etc.) are trusted for temporal routing | [partially validated] — R5 gates on `question_type`; a KU question phrased temporally gets no temporal handling |
| The vector leg is "stronger post-swap" than pre-swap on the hybrid arm | [validated] — hybrid 0.786/0.598 > pre-swap baseline; vector arm 0.7294/0.5649 |

### Boundary & stakeholders
- **Out of scope:** #317 reranking (CE/GPU-serving), embedder dimension upgrade (nomic 768 / Qwen3 1024 — #265 coordination), extractor changes, reader/judge changes, production behavior change (all levers default-off until decided).
- **Affected but unmentioned:** `sdk.tortoise_fts_query` API consumers (MCP server, SDK users) — any fusion signature change must be additive/default-off; the `fallback_snapshot` cache (L4 uses it — write-invalidation correctness matters); `mini_beir` (OOD transfer check per lever).

### Problem-diverge verdict
Framing **C** (root-cause: fusion dilution + lexical brittleness) is the confirmed problem, with **B**'s interaction-aware measurement protocol. The issue's 4-lever list survives but is re-ordered by evidence: **L3 (fusion-fix) and L4 (TF-IDF leg) address root causes; L1 and L2 are refinements** (L2's real delta is extending temporal intent to KU, not the query-side injection).

---

## Phase 2 — problem-converge: confirmed problem definition

> **Confirmed problem:** The post-swap hybrid retrieval (turn_recall@10 = 0.786, nDCG@10 = 0.598) is diluted by equal-weight RRF fusion of a *strong* vector leg with weaker FTS/structural legs (measured −1.95 nDCG pts when fusion was applied to a strong vector leg on the embedded surface), and the sparse leg remains brittle on paraphrase (probe C: FTS zeroed a point TF-IDF surfaced at rank 1). The highest-value, 300ms-budget-compatible levers are: (1) fusion re-weighting (per-leg weights/k, measured), (2) an always-on TF-IDF lexical leg via the cached snapshot, then (3) search_keys-based key expansion and (4) temporal-intent extension — in that order, measured jointly where they interact, each default-off in production until a pre-registered bar is met.

**Why this framing:** the paired deltas (fused < vector, CI excludes 0) and probe C are the only *measured* in-repo evidence; L3 and L4 attack them directly. L1/L2 have weaker external deltas (benchmark-inflated / already-shipped-retrieval-side) and are cheaper to defer.

**Rejected framings:** A alone (would run 4 independent experiments and mis-attribute the L3×L4 dilution interaction); D (reader-saturation is not reached at our recall level).

**Falsification check:** if a per-leg weight sweep on the HNSW surface shows no config beating equal-weight RRF beyond noise, and an always-on TF-IDF leg shows no pool-membership change, then the root-cause framing is wrong and the bottleneck is elsewhere (e.g., pool depth, or the structural leg's scoring) — the plan includes a negative-result path.

**Confidence: 75/100** (in-repo evidence is solid for the mechanism; the real-surface weight-sweep outcome is unmeasured until we run it).

---

## Phase 3 — codebase explorer summary (verified paths)

| Surface | File | What changes |
|---|---|---|
| Fusion | `tortoise/search_engine.py::rrf_fusion` | add `leg_weights` param (default None → byte-identical); optional per-leg k |
| Degradation chain | `degradation_chain` | 4th leg wiring (TF-IDF via snapshot) OR FTS-blend fold |
| Query encode | `tortoise/sdk.py::tortoise_fts_query` (query_vec block) | L1/L2 query pre-transform hook (search_keys PRF / date injection) — additive, default-off |
| TF-IDF index | `tortoise/fallback_snapshot.py` (snapshot_key/build_snapshot/search_snapshot) | reuse as the L4 index source; digest-keyed, write-invalidated |
| Eval harness | `tools/longmem_eval/retrieve.py::hybrid_search/_vector_retrieve`, `run.py::run_evaluation` | per-lever knobs (R6 pattern: env/CLI, default-off, fingerprint-keyed checkpoints) |
| mini-BEIR | `tools/mini_beir/run.py` | OOD transfer per lever config |

**Partial implementations that matter:** R5 recency_boost plumbing = the exact pattern for a `leg_weights` knob thread-through (recency_field/recency_boost → `_recency_factors` → rrf_fusion multiplier, all default-off, byte-identical); R6 rerank stage = the default-off eval-surface precedent with fingerprint-gated checkpoints; E3 #1535 already annotates `search_keys` per hit "for R2's future query-expansion consumer" — the consumer hook is *designed in, unimplemented*.

---

## Phase 4 — solution-diverge: distinct approaches

**Approach 1 — "Weighted RRF with knobs" (fusion-first, minimal surface):** add `leg_weights` (+ optional per-leg k) to `rrf_fusion`, thread via `tortoise_fts_query`/`hybrid_search` as env/CLI knobs (default None = equal weights, byte-identical). Measure a k×weight grid on the HNSW surface. L4 = fold TF-IDF hits into the FTS leg's list pre-fusion (blend), avoiding a 4th degradation-chain leg.
- *Risks:* weight overfit on one surface (OpenSearch drift caveat — mitigate: keep weights config, re-measure per release); FTS-blend muddles leg attribution in `match_source`.
- *Best fit if:* the weight sweep shows a monotonic preference and we want the smallest production footprint.

**Approach 2 — "Always-on 4th TF-IDF leg + weighted RRF" (recall-first, competitor-faithful):** TF-IDF becomes a real 4th strategy in `degradation_chain` (snapshot-backed), fused by weighted RRF; `match_source` gains a `tfidf` leg bucket (already a legal value). L1/L2 as query-transforms. This is the mem0/graphiti shape (dense-first + always-on sparse).
- *Risks:* 4th leg dilutes vector further unless weights are swept jointly (interaction — controlled by the joint design); snapshot staleness; slight pool/footprint growth.
- *Best fit if:* probe-C-style paraphrase misses are confirmed on the real surface and recall (not ordering) is the binding metric.

**Approach 3 — "Score-aware fusion" (abandon rank-only):** replace/augment RRF with min-max normalized cosine + weighted sum (OpenSearch normalization-processor pattern). Preserves the vector leg's score magnitude (Redis critique: RRF can't distinguish 0.99 vs 0.51 at rank 1).
- *Risks:* the exact fragility RRF exists to avoid (score-distribution drift between legs; FTS scores unbounded → normalization coupling); larger behavior change to the core; contradicts the established RRF baseline.
- *Best fit if:* the weight sweep shows equal-weight RRF cannot express the desired vector dominance — i.e., evidence the *mechanism* (rank-only) is the problem, not the *weights*.

**Rejected for this scope:** Approach 3 (defers to a follow-up unless the L3 sweep fails — keep RRF's scale-invariance; the judged data justifies weights, not a fusion rewrite). HyDE-in-hot-path (LLM cost, budget-excluded). Doc2Query/document-side expansion (index-side change = re-ingest event, out of scope).

---

## Phase 5 — solution-converge: recommended approach

**Chosen: Approach 2 with Approach 1's knob discipline** — always-on snapshot TF-IDF 4th leg (or FTS-blend where attribution matters) + weighted RRF with config knobs, all default-off, measured jointly. Rationale: (a) it addresses both root causes (fusion dilution + lexical brittleness) with the only in-repo-measured mechanisms; (b) every competitor (mem0, graphiti) runs dense-first + always-on sparse — the pattern is externally validated; (c) the R5/R6 knob pattern already proves the byte-identical default-off contract in this exact codebase.

**Implementation shape (deferred to writing-plans, this scope decides WHAT and MEASURES):**
- `rrf_fusion(..., leg_weights=None, per_leg_k=None)` — additive, default None = byte-identical.
- 4th leg: snapshot-backed TF-IDF in `degradation_chain` (or pre-fusion FTS-blend fallback — decision point, see open questions).
- Query-transforms (L1 search_keys PRF; L2 temporal-intent extension) as a pre-encode hook in `tortoise_fts_query`, default-off.
- All knobs env/CLI-exposed on `tools/longmem_eval/run.py` with fingerprint-keyed checkpoints (R6 precedent) so every lever config is a distinct, resumable, provenance-carrying run.

---

## Measurement plan (the harness, pre-registered)

**Primary surface:** LongMemEval-S, Docker/HNSW (`python -m tools.longmem_eval.run --split s --db <uri> --retriever hybrid`), co-primary **turn_recall@10 + nDCG@10** — same gate contract as #1349 (BH-FDR q=0.10 over the lever set; the checkpoint/fingerprint machinery makes each config a distinct `{surface}__{retriever}__{model}__{prompt}` run).
**OOD transfer:** `tools/mini_beir/run.py` per lever config — NFCorpus/SciFact/FiQA weighted (MS MARCO is in-domain for bge), binary-gain nDCG/R@10 identical metric definition.

| Lever | Experiment | Configs | Pre-registered bar (per issue target) | Sample size |
|---|---|---|---|---|
| L3 fusion-fix | k×weight grid on HNSW hybrid | k ∈ {40,60,80} × w_vec ∈ {1.0,1.5,2.0} × w_fts ∈ {0.5,1.0} × w_struct ∈ {0.5,1.0} (coarse first, refine around the winner) | ≥ +3% relative nDCG@10 vs 0.598 (≈ ≥0.616), no turn_recall@10 regression > 1 pt | 500-Q split s |
| L4 TF-IDF leg | 4th-leg on/off × the L3 winner weights (joint — controls dilution) | tfidf ∈ {off, on} × best weights | ≥ +3% relative turn_recall@10 (0.786 → ≥0.810) OR nDCG | 500-Q |
| L1 key-expansion | search_keys PRF (top-k pool aliases → FTS terms + 1 augmented embed) on/off | expand ∈ {off, prf} × leg ∈ {fts, dense, both} | ≥ +3% relative turn_recall@10; no nDCG regression | 500-Q |
| L2 time-aware | (a) temporal-intent extension to KU/non-TR; (b) query-side "as of {qdate}" injection | intent ∈ {tr-only, all-cats} × inject ∈ {off, on} | ≥ +3% relative on the KU/TR category split (per-category pre-registered) | 500-Q (category-stratified) |
| Baseline re-measure | equal-weight RRF, no levers — must reproduce 0.786/0.598 within CI | — | reproducibility gate: Δ < 1 pt | 500-Q |

**Control discipline (R6 precedent):** every lever OFF by default = byte-identical baseline path; per-question `legs` trace + `match_source_counts` already record leg provenance (M7 #1527) — the L4 leg-mix bucket answers "which leg found what" per lever run. `--mock` CI smoke stays runnable; real runs refuse to start without the embedder (R3 pre-flight). mini-BEIR runs are advisory, not gates (per its own README).

**Negative-result path:** if no weight config beats equal-weight RRF beyond noise on the HNSW surface, the fusion-dilution root cause is falsified at the real embedder's operating point → scope re-focuses on L4/L1 (recall) and files the fusion question as a score-aware-fusion follow-up (Approach 3). The scope explicitly pre-registers this.

---

## Complexity (domain-aware)

| Domain | Rating | Rationale |
|---|---|---|
| Research | **standard** | multi-lever investigation with pre-registered measurement; per-lever implementation deferred |
| Architecture | **standard** | fusion signature + degradation chain + query-transform hook touch the retrieval core (sdk/search_engine); all changes additive/default-off |

No UX dimension (no UI surface; reader-context rendering unchanged for baseline-off). No ontology dimension (no new entity kinds/properties — `search_keys` exists; temporal fields exist).

---

## E2E test approach

- **Lever isolation E2E (per lever):** a config run with the lever ON must produce a checkpoint with the lever's fingerprint key; a run with the lever OFF must reproduce the baseline report byte-for-byte at the outcome shape level (`_fingerprint_diffs` gate — stale/contaminated resume refused).
- **Production-safety E2E:** default-off invariant — `tortoise_fts_query` with no lever knobs set emits byte-identical output to today (the existing `tests/eval/retrieval/baseline/baseline-embedded-2026-08-17.json` comparison pattern); unit tests pin `rrf_fusion` equality when `leg_weights=None`.
- **Leg-provenance E2E:** L4's TF-IDF hits carry `match_source="tfidf"`; the never-null leg-mix contract (E2E-1 #1540) holds with the 4th leg — `sum(provenance legs) + dropped == pool_size` partition preserved.
- **L2 E2E:** a KU question with implicit temporal intent gets the recency path (unit test on `detect_time_constraint` extension); an explicit-date question does NOT get fresh-biased weighting (the "invert" rule).
- **Degradation E2E:** with the embedder absent, the hybrid must still serve (TF-IDF leg becomes primary — the hard-tier path), and `MODEL_ENCODE_FAILED` semantics for the *vector arm* stay untouched (L4 must not silently turn a vector-arm run into a TF-IDF run).
- **mini-BEIR:** per-lever config runs as OOD transfer checks (advisory).

---

## Sequencing vs #317 and the 300ms budget

| Path | Latency | Status |
|---|---|---|
| #317 cross-encoder rerank (CE, CPU top-10) | ≈ +210ms/top-10 — violates 300ms E2E | **blocked** on GPU/API-served inference (unchanged; R6 rerank stage stays default-off as the future measurement surface) |
| **#1657 levers (this scope)** | L3 ~0ms; L4 ~1–5ms cached; L1 ~0–5ms rule-based; L2 ~0ms — **all inside budget** | this scope |

Order: **measure L3 → L4 (joint) → L1 → L2**, each producing a go/no-go implementation issue with measured deltas (the issue's Indicator 2). Each approved lever then ships as its own implementation issue (decompose step) with the default-off knob flipped on only after the measured decision. Production release of an approved lever is a separate commit-workflow/verification step — not part of this scope.

---

## Open questions (human decisions)

1. **L4 leg architecture:** 4th `degradation_chain` leg (clean, competitor-faithful, but a new breaker/timeout surface) vs pre-fusion FTS-blend (smaller footprint, muddier leg attribution). Recommend: 4th leg — the eval already distinguishes `tfidf` match_source and the snapshot infra exists.
2. **Weight calibration cadence:** tuned weights drift when the embedder/corpus changes (OpenSearch caveat). Recommend: weights stay env/config knobs (like `recency_boost`), re-measured at each embedder-affecting release — accept or add a CI re-measure hook?
3. **L1 LLM-based expansion:** HyDE is excluded from the hot path, but should the *research* scope measure HyDE headroom on the eval surface (using the eval's out-of-loop reader model as the generator) to size a future local-model variant? (Adds a research-only experiment, no production surface.)
4. **L2 scope boundary:** is extending temporal intent beyond the TR category (KU recency) in-scope for #1657, or should it be its own issue? It shares `detect_time_constraint` but touches non-TR retrieval semantics.
5. **Baseline reproducibility run:** re-run the hybrid baseline on the current branch before lever experiments (issue baseline is 0.786/0.598 from the gate) — confirm within ±1 pt before trusting lever deltas. Approval to spend the ~1 full eval run (LongMemEval-S Docker, retrieval-only ≈ hours) on this control.

---

## Wiring check (preliminary)

| Touch Point | Type | Covered By |
|---|---|---|
| `search_engine.rrf_fusion` signature | core | additive `leg_weights`/`per_leg_k`, default None — unit-pinned |
| `degradation_chain` | core | 4th-leg wiring (L4) with breaker + timeout + leg trace |
| `sdk.tortoise_fts_query` | core API | query-transform hook + knob thread-through, default-off |
| `fallback_snapshot` cache | infra | L4 index source; digest-keyed write-invalidation correctness |
| `tools/longmem_eval/run.py` + `retrieve.py` | eval | per-lever knobs, fingerprint-keyed checkpoints, report provenance |
| `tools/mini_beir/run.py` | research | OOD transfer per lever |
| MCP/SDK consumers of `tortoise_fts_query` | downstream | no behavior change while knobs default-off — verify via existing search tests |

---

## Deliverables status

- [x] `research.md` — per-lever mechanism/hypothesis/cost-benefit/external precedent (in `/tmp/followup-docs/1657/`)
- [x] `scoping.md` — this document
- [ ] (deferred) per-lever implementation issues with measured deltas — after lever experiments run
- [ ] (deferred) plan doc via writing-plans — for the first approved lever

**Recommended first implementation:** the **fusion-fix (L3) weight sweep** — it has the highest evidence-to-cost ratio (paired deltas already measured), a ~0ms cost, the smallest surface (`rrf_fusion` signature + one knob thread), and it must be measured *before* L4 so the joint dilution interaction is controllable. A single LongMemEval-S Docker retrieval-only run over the k×weight grid (with the reproducibility control run) is the first step.
