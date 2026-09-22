<!-- research-path: docs/epics/2026-08-20-1509-extractor-v3/02-research-brief.md -->

# #1745 — Point-Level Evidence Recall 0.27 vs 0.55 Chunk-Level: Reader Context Losing Half the Evidence

> **For Pi:** Use `executing-plans` to implement this plan task-by-task. Scoping output from `issue-scoping` (double diamond + 2 verification gates) — plan = confirmed problem + ranked root-cause hypotheses + fix design + test strategy. **Do NOT implement outside this plan.**

**Goal:** Close the point-level evidence recall gap (evidence@20 0.27 vs chunk-evidence@20 0.55) so the reader's context carries more of the question's evidence. Per the pilot deep-analysis, this is the largest *retrieval-side* lever; the *accuracy* ceiling on the healthy population is reader-side (see §1.2).

**Team:** epistemic-team · **Complexity:** standard (Architecture) · **Epic:** #1509 (extractor-v3) · **Depends on:** none (may interact with #1695 — coordinated, see §Coordination)

**Targets (from the issue, re-based on the clean single population per deep-analysis):** (1) evidence@20 ≥ 0.45 on the clean 50-Q re-validation (fresh-only baseline 0.256 → gap is real); (2) turn@20 ≥ 0.40; (3) vacuity ≤ 0.10 (fresh-only baseline is already 0.033 — measured as a guard, not a chase); regression: session@20 ≥ 0.90 (fresh-only baseline 1.00), accuracy ≥ 0.74.

---

## 1. Confirmed Problem (Phase 2 — problem-converge)

### 1.1 The funnel (what the numbers actually say)

The retrieval funnel: session@20 **0.90**, turn@20 **0.24**, evidence@20 **0.27**, chunk-evidence@20 **0.55**. The reader's context is **fixed 20 points / ~2,281 tokens mean** — the 8,000-token budget never binds (max 5,099), so the context is ITEM-capped, not budget-capped, and the chunks that retain ~2× the evidence are structurally starved from the reader context by points-first assembly.

**The dominant seam (H1, code-verified):** `_assemble_context` in `tools/longmem_eval/retrieve.py` partitions the pool into points-then-chunks and slices `(points + chunks)[:top_k]` with `top_k=20`. **All points precede all chunks regardless of RRF rank**, so any pool with ≥20 points (true for 50/50 outcomes — `context_point_count` is exactly 20.0 everywhere) starves the chunks entirely, and the token budget never binds. This is a *context-assembly* bug, not a retrieval-ranking bug: chunk evidence is present in the pool at 0.55 but dropped before the reader sees it.

### 1.2 ⚠️ Baseline integrity — the issue's headline numbers are a two-population blend

The `pilot-deep-analysis.md` (same worktree, read in full during scoping) proves the reported 0.74 accuracy / 0.27 evidence@20 / 0.14 vacuity are **not a single-population estimate**:

| Population | n | acc | sess@20 | ev@20 | chunk@20 | vacuity | FTS-empty |
|---|---|---|---|---|---|---|---|
| **Resumed** (pre-crash checkpoint reuse) | 20 | 0.55 | 0.75 | 0.292 | 0.516 | 6/20 | **13/20** |
| **Fresh** (post-crash 5-worker run) | 30 | **0.867** | **1.00** | 0.256 | 0.567 | **0.033** | **0/30** |
| Blended (the issue's headline) | 50 | 0.74 | 0.90 | 0.268 | 0.547 | 0.14 | 13/50 |

Consequences for this issue:
1. **FTS-empty (13/50) is a crash artifact, not a paraphrase gap** — 13/13 in the resumed set, 0/30 fresh (`falkordb-crash-investigation.md`: MISCONF under OOM memory pressure). The C3 query-expansion premise in the original hypothesis (c) is **data-false on the healthy population** → C3 demoted to verify-first (§4.3).
2. **Vacuity 0.14 is contaminated** — 6/7 vacuous outcomes are resumed artifacts; 5 of them have `evidence_recall@20 = None` (ingest stats claim 7–15 marks, retrieval-time graph query finds 0 — an internal inconsistency that exists **only** in the resumed set). The single fresh vacuous question (`0862e8bf`) was **answered correctly** via chunk containment (`chunk@20=1.0`, label=True) — proof the chunk leg is a working safety net.
3. **The genuine gap survives on the clean population:** fresh ev@20 = 0.256 vs chunk@20 = 0.567 — the retrieval fix target is real, just ~19pp below the blended number's implication.

### 1.3 Rejected framings (documented)

- *"The extractor is failing"* — rejected: extractor evidence coverage is 1.0 (all questions have evidence points; min 4/question), and chunk containment (which requires NO extraction) shows the same evidence is retrievable at 0.55+. The loss is on the read path, not the write path.
- *"The vector embedder is broken"* — rejected: `embedding_coverage` is 1.0 on all outcomes; the vector leg returns 120 hits on every question.
- *"Vacuity is a data problem"* — rejected: 6/7 vacuous are resumed artifacts; the fresh one was answered correctly via chunks.
- *"The reader is the bottleneck, so retrieval fixes don't matter"* — **partially TRUE and explicitly NOT absorbed into scope** (§4.5): on the healthy population, 4/4 fresh wrongs are reader-side over-abstention (`6f9b354f` abstained with **ev@20=1.0, 8/8 evidence points in context**). This is a *separate* accuracy lever owned by reader-prompt work (A1 calibration, deep-analysis finding #3) — recommended as a filed issue, not absorbed here (see §9). Retrieval-side fixes still move the evidence/vacuity metrics this issue targets, and chunk-surfacing (C1) reduces the *retrieval-side* abstention causes (T2 cluster).

### 1.4 The accuracy-lever claim — qualified

The issue title calls this "the largest accuracy lever." The deep analysis qualifies it: on the **blended** population, retrieval-dead is 38% of wrongs and reader-abstention 38%; on the **fresh** population, retrieval is not the bottleneck (100% session recall, all 4 failures reader-side). This plan therefore scopes to the metrics the retrieval fix genuinely moves (evidence@20, turn@20, chunk-in-context, retrieval-side vacuity) and treats accuracy strictly as a **regression guard** (≥ 0.74), not the primary acceptance criterion. The reader-commitment ablation is filed separately (§9).

---

## 2. Ranked Root-Cause Hypotheses (with evidence)

### H1 — [CONFIRMED, DOMINANT] Context assembly starves chunks: points-first partition + 20-item cap
**Mechanism:** `_assemble_context(pool, top_k=20, max_context_tokens=8000)` in `tools/longmem_eval/retrieve.py` (lines ~616–634) re-partitions the RRF-ranked pool into `points = [h for h in pool if not _is_raw_chunk(h)]` and `chunks`, then iterates `(points + chunks)[:top_k]`. All points precede all chunks **regardless of RRF rank**. When the pool has ≥20 points (true for all 50 outcomes — `context_point_count` is exactly 20.0 everywhere), chunks never enter the reader context.

**Evidence:**
- `context_tokens_mean` = 2,281 vs cap 8,000; max 5,099 — the token budget is never the binding constraint. If chunks were backfilling (the R1 #1540 design intent: "extracted points render in rank order, raw chunks backfill the remaining context_token_cap tokens"), the mean would approach the cap.
- `chunk_evidence_recall@20 = 0.55` is computed over `pool[:20]` (the DEDUPED POOL, `ret["hits"]` — `_recall_metrics(pool, ks=ks, ...)` at retrieve.py ~1070), NOT the reader context — chunk evidence IS in the top-20 pool but is dropped by assembly.
- Direct simulation of `_assemble_context` with a 60-item pool (30 points + 30 chunks) → **0 chunks in the 20-item context**.
- Outcome `0862e8bf` (the only fresh vacuous): `chunk_evidence_recall@20=1.0`, `evidence_recall@20=0.0`, `session@20=1.0`, **`label=True`** — the reader answered correctly from the CHUNK (verbatim evidence), while the marked extracted points never surfaced. This proves chunks carry answerable evidence and are the recovery floor.
- Deep-analysis finding 6: **22/50 questions have `chunk_evidence_recall@20 − evidence_recall@20 ≥ 0.5`** (16 fresh) — the raw chunk carrying the answer surfaces while only a minority of marked points do.

### H2 — [CONFIRMED] Marked evidence points rank below pool[:20] (retrieval ranking gap)
**Mechanism:** evidence marks (`has_answer`) are written at ingest and read at annotation time (`_annotate_hits` reads `has_answer` from point props) but **never influence ranking** — the RRF fusion in `tortoise_fts_query` ranks on content similarity only. Marked extracted points (short paraphrased statements) rank below the top-20 boundary even when the session is found.

**Evidence:**
- `evidence_recall@20 = 0.27` — 73% of marked points fall outside `pool[:20]` despite `session@20 = 0.90`.
- Deep-analysis: mean **2.34 of ~9.4 marked points** surface in the top-20. On fresh data, `6f9b354f` reached ev@20=1.0 (all 8 marked points surfaced) — showing the ceiling exists but is not the norm.
- **Metric placement (critical for the fix design):** `_recall_metrics(pool, ...)` runs at retrieve.py ~1070, `_assemble_context` at ~1078. **`evidence_recall@k` measures the POOL, not the reader context.** C1 (context assembly) therefore cannot move the pool-based `evidence@20` metric at all — only C2 (boost, placed before `_recall_metrics`) or C3 (pool composition) can. The issue's acceptance metric and the reader's actual context are different surfaces (§4.6 reconciles this).

### H3 — [CONFIRMED] Evidence-mark dilution: ~99% of marks are source-session attribution
**Mechanism:** M6 mark (a) "source-session attribution" fires for ANY point written from an evidence-bearing session. The pilot shows **472/479 evidence marks (98.5%) are source-session**; only 6 verbatim and 1 raw_chunk (deep-analysis: "only 6/50 questions got a precise verbatim mark, 1/50 a raw_chunk mark"). A session with a single `has_answer` turn marks ALL of its extracted points (mean 9.4, range 4–17) as evidence — the denominator inflates, and `evidence_recall@k` measures "fraction of the answer session's points that surfaced," not "fraction of the answer's information recovered." The precise raw-chunk containment view (0.55) is closer to true answer availability.

**Evidence:** per-question `evidence_marks` breakdown in the checkpoint: e.g. `e47becba` — 8 evidence_points, marks `{source_session: 8, verbatim: 0, raw_chunk: 0}`. Aggregate across all 50: `{source_session: 472, verbatim: 6, raw_chunk: 1}`.

**Consequence:** the 0.27 metric is an *underestimate* of answer availability AND an inflated denominator — both. The fix should not change the denominator (comparability, D5 #1540) but must be precision-aware when boosting (§4.2), and the deep-analysis recommends adding an answer-string mark for a future metric re-baseline (filed separately, §9).

### H4 — [CONFIRMED] Reverse starvation: `max_chunks_per_session=2` caps out the evidence chunk
**Mechanism:** the R1 per-session chunk dedup (D3 #1540, cap 2/session, `_dedup_pool` retrieve.py ~425–443) keeps the top-2 chunks per session in rank order. On **18 questions**, `chunk_evidence_recall@20=0` while `evidence_recall@20` is high (e.g. `21436231` ev 0.706 / chunk 0.0) — the *evidence* chunk was capped out in favor of higher-ranked non-evidence chunks from the same session.

**Evidence:** deep-analysis §Point-level loss; across all 854 evidence sessions in the S-split only **4** have >2 marked chunks (chunk_turns=2 windows) — raising the cap to 3 is nearly free and directly lifts chunk evidence in the reader context with bounded budget cost.

---

## 3. External Research (Phase 1.5 — Axis Research)

> **Findings date:** 2026-08-26. **Domain:** Complicated (retrieval engineering, strong external precedent). **Axes:** Architecture=standard (drives this research), Ontology=low, UX=low.

| Axis / Framing | Findings | Source (canonical / competitor / pitfalls) |
|---|---|---|
| Chunk vs artifact granularity | Verbatim chunks beat LLM-extracted artifacts by **15.9pp (LoCoMo) / 22.0pp (LongMemEval-S)**; the union (chunks ∪ artifacts) matches chunks — artifacts add retrieval noise once verbatim text is present; a 1-hop semantic graph does NOT recover the gap. Lossy distillation is the mechanism ("structure should augment verbatim text, not replace it"). **Our pilot reproduces this exactly: 0.55 vs 0.27.** | arXiv 2601.00821 (canonical, already in epic brief §"Verbatim Chunks Beat Extracted Artifacts") |
| Evidence-passage retrieval remedies | Low-recall remedies in production RAG: (1) retrieve more candidates (recall is a top-k problem), (2) hybrid search (BM25 + dense) — we have RRF already, (3) **rerank with an evidence-aware signal** (post-fusion), (4) query expansion / rewriting for weak embeddings. | Law Zava RAG strategies; Confident AI RAG eval guide; Scribd RAG remedies (competitor-precedent, aggregated) |
| Top-k / context budget (pitfalls framing) | Optimal retrieved-passage count is a saturation curve: more passages help until reader distraction overtakes evidence accrual; systems commonly retrieve 50–200 candidates then pass 5–12 to the reader; relevant-passage RANK degrades as top-k grows. **Pitfall for C1:** an unceilinged 60-item context (~6.8k tokens at measured ~114 tok/item) may sit BELOW the 8k budget — the budget may not bind, and reader flood becomes the risk (see H1 fix design's item ceiling). | Retrieval Saturation (tmls.nyc); ACL 2025 "Shifting from Ranking to Set Selection"; arXiv 2411.07396 |
| LongMemEval official baselines | LongMemEval paper: expanding memory keys with extracted user facts improves memory recall ~4–9% and QA ~5% — extraction HELPS when it augments, not replaces, verbatim. Full-context GPT-4o ≈ 60.2%; sustained-memory drop ~30%. Our per-question isolated corpus is a documented variant (methodology records this). | arXiv 2410.10813; xiaowu0162.github.io/long-mem-eval (canonical) |
| Adversarial frame (this issue's own metric) | Deep-analysis: **no threshold on evidence_recall@20 predicts correctness** (the 0.25–0.5 bin outperforms 0.5–1.0; correct answers at ev@20=0.091; wrong at 1.0). session_recall@20 is the only hard gate. → evidence@20 is a recall metric, not an accuracy proxy; the plan's accuracy role is regression-guard only. | pilot-deep-analysis.md (in-repo, findings date 2026-08-26) |

**Research-driven design consequences:**
1. Chunks must reach the reader context — the union (points ∪ chunks) is the evidence floor (H1). Fix = budget-driven rank interleave, not points-first.
2. Marked points need a ranking assist, not just a larger cap (H2) — evidence-aware boost post-fusion, precision-aware (H3).
3. Top-k saturation pitfall → C1 needs an explicit context-item ceiling alongside the token budget, and the reader-flood risk on TR/KU categories must be pinned (§4.1, §5).
4. The 13/50 FTS-empty premise is crash-confounded → C3 is verify-first, not a shipped fix (§4.3).

---

## 4. Fix Design (Phase 4–5 — solution-converge)

**Chosen approach — "Evidence-surfacing context + ranked mark boost + clean-run gate"** (C1–C4, priority order).

### C1 — Budget-driven, rank-interleaved context assembly (fixes H1, the dominant seam)
Change `_assemble_context` in `tools/longmem_eval/retrieve.py`:
- **Interleave points and chunks in true RRF rank order** (single pool iteration, no points-first partition) — a chunk ranked #5 in the pool enters the context at its rank, not after all points.
- **Let the 8,000-token budget be the binding constraint** with an **explicit `context_item_cap`** (default 40; knob `TORTOISE_LME_CONTEXT_ITEMS`) — the saturation research warns against an unceilinged whole-pool walk, and the measured ~114 tok/item means a 60-item pool ≈ 6.8k tokens may not bind the 8k budget. The item cap bounds reader flood while the token budget selects within it. `context_point_count` becomes budget+item derived.
- **TR branch pinned:** temporal-reasoning questions keep R5's `tr_top_k=12` item cap (the transcript-flood control the pipeline already paid for — 9/18 TR losses were reader refusals under ~40k floods). C1's interleave applies *inside* the TR cap; the budget walk does NOT silently undo R5. Test-pinned (§5, S28).
- Keep: `_render_block` exact token accounting, skip-not-starve oversized hits, per-session chunk dedup (D3 #1540), TR time-ascending render (R5 #1544), `context_tokens == _estimate_tokens(render_context(...))` alignment invariant.
- **Decision record:** this replaces UX decision 3 (points-first) from R1 #1540 with rank-order interleave — the pilot data (0.55 chunk evidence starved) is the evidence that points-first cost recall; the R1 plan's own research (Verbatim-Chunks union verdict) supports the reversal.
- **Contract note (P3 from verifier):** the module docstring's "recall@k computed over the DEDUPED pool… reflects what the reader could actually see" becomes an upper bound after C1 — update the docstring; C4's `reader_evidence@k` becomes the "what the reader saw" surface.

### C2 — Evidence-mark boost at retrieval (fixes H2; the ONLY pool-metric mover)
Placement is **pinned relative to the metric** (verifier P1-1): the boost applies to the annotated pool **BEFORE `_recall_metrics`**, so post-fix `evidence_recall@k` is honestly "evidence recall over the boosted pool" — stated in the methodology string, and the pre-boost ranking is preserved for the `reader_evidence` diagnostic ablation (C4). Implementation:
- **Stable rank-offset re-rank, not an RRF-score multiplier** (verifier P1-6: annotated hits drop `scores.rrf` — there is no score to multiply). Implement `_apply_evidence_boost(pool, *, boost_verbatim, boost_source)` that moves marked hits up by a bounded rank offset, never demoting unmarked hits below their relative order, never reordering within the marked class.
- **Precision guard re-spec (verifier P1-4 — the OR'd `has_answer` cannot express the verbatim-vs-source split):** the per-mark breakdown is not stored on points (only in ingest stats). Two options, pick **read-time recomputation** via `evidence.py:mark_for` — the annotated hits already carry `content`/`quote`/`session_id`, and the question carries `haystack_sessions`, so the three marks are recomputable at read time without a graph change. (Fallback documented: store the mark breakdown as an additive eval prop at ingest — only if read-time recompute proves too slow; a graph-prop change is acceptable for eval-instrumentation but is the second choice.)
- **Multipliers:** verbatim/raw_chunk marks (the precise ones) full boost (default ×1.5 rank offset); source-session-only points reduced (×1.15). Knobs: `TORTOISE_LME_EVIDENCE_BOOST` (1.5), `TORTOISE_LME_EVIDENCE_BOOST_VERBATIM` (1.5), `TORTOISE_LME_EVIDENCE_BOOST_SOURCE` (1.15).
- **Default contradiction resolved (verifier P1-1):** boost is **ON for the re-validation run** (Task 6 sets the env explicitly) and OFF by default in code until the re-validation passes — no §4-vs-Task-2 ambiguity. The acceptance gate is stated as "boost-enabled run," the ablation (C4) reports pre/post so attribution is visible.
- **Chunk-vs-point competition guard (verifier P1-1c):** a synthetic test asserts boosted chunks do NOT push marked points out of top-20 — the boost must be additive to evidence, not a redistribution between evidence classes.
- **Not chosen:** enabling the R6 cross-encoder rerank as the primary mover — heavier (model call per query), evidence-blind, and the saturation research warns top-k rerank alone doesn't fix recall. R6 stays a follow-up lever; if R6 is ever enabled, C2's stage order is **boost-before-rerank** (documented, P3 from verifier).

### C3 — Sparse-leg query expansion — DEMOTED to verify-first (fixes nothing on the clean population as specced)
**Premise correction (verifier P1-3, deep-analysis):** the 13/50 FTS-empty is 13/13 crash-run artifacts (0/30 fresh) — C3's "paraphrase gap" justification is **data-false on the healthy population**. Also `search_keys` is extractor-emitted with no D3 deterministic fallback (ingest_v2.py writes `p.get("search_keys") or None`), so expansion coverage is unverified.
**Rescoped C3:** a **diagnostic task**, not a shipped fix — (a) measure FTS-empty rate + `search_keys` population rate on the clean re-validation; (b) if a genuine paraphrase gap appears on clean data, then build `expand_query_with_search_keys` (union top-vector-hit search_keys into the OR query, max 12 terms, fail-open, knob `TORTOISE_LME_QUERY_EXPAND`); (c) otherwise, do not ship expansion. This removes a phantom-failure fix and keeps the option behind a knob.

### C4 — Reader-context evidence diagnostic + report hygiene (the honest gate)
- **New `reader_evidence@k`** in `retrieve_for_question`: fraction of evidence-marked hits actually present in `context_points[:k]` / marked total — this is the metric C1 actually moves, and the pool→context drop becomes directly measurable. **This is the primary acceptance metric for C1's effect** (the issue's pool-based `evidence@20` is the secondary record with the pre/post-boost ablation).
- Promote the per-question `evidence_marks` breakdown into the report aggregation (the checkpoint already carries it per question — verifier P4-1 verified `ingest.evidence_marks` present; only the report-level aggregate is missing).
- **Populate `ranked_ids` / `evidence_turn_matches`** (currently 0/50 — the pilot's context composition is unreconstructable, deep-analysis finding 5). This is a **Task-0 prerequisite** for C2 calibration and C4.

### C5 — `max_chunks_per_session` 2→3 (fixes H4, cheap and near-free)
Raise the R1 dedup cap default from 2 to 3 (`DEFAULT_MAX_CHUNKS_PER_SESSION`), knob-exposed. Evidence: only 4/854 S-split sessions have >2 marked chunks — the budget cost is bounded; the benefit is the ~18 questions where the evidence chunk was capped out (chunk@20=0 with ev@20 high). Regression-guarded (per-session chunk count in pool/context ≤ 3, session recall stable).

### 4.5 Not absorbed (filed separately, see §9)
- **Reader over-abstention (A1 clause calibration)** — the healthy population's 4/4 fresh failures; retrieval fixes do not move it. Filed as a separate issue with the deep-analysis C2-cluster evidence.
- **Answer-string evidence mark / metric re-baseline** — deep-analysis recommendation 4; separate metric change, not a retrieval fix.
- **Resume-quality gate + single-population re-validation discipline** — belongs to the run protocol / capstone (#1549), folded into Task 6's protocol requirements here (this issue's re-validation must adopt it), and recommended as a protocol issue.

### 4.6 Metric-vs-context reconciliation (the plan's gate design)
- **`evidence_recall@k` (pool-based, issue's metric):** moved by C2 (+ C3 if it ever fires), measured over the boosted pool — stated in methodology.
- **`reader_evidence@k` (context-based, new):** moved by C1 — the honest measure of "evidence the reader could see."
- **Vacuity / session@20:** regression guards.
- The issue's target "evidence@20 ≥ 0.45" is satisfied via C2's boost (pool-level) AND C1's context-surfacing is proven via `reader_evidence@k` — both reported in the re-validation.

---

## 5. Integration Surface Map (test-design #1515 surfaces)

| Surface | Component | Change | Test layer |
|---|---|---|---|
| S25 Reader context format (UX-3 → rank-interleave) | `retrieve.py::_assemble_context` | C1: budget+item-capped rank interleave, no points-first; TR keeps tr_top_k | unit (pure assembly) + integration (embedded mini) |
| S19 Evidence marking (M6) | `retrieve.py::retrieve_for_question` + `evidence.py` | C2: read-time mark recompute + rank-offset boost; C4: reader_evidence@k | unit (boost math + mark recompute) + integration |
| S11 FTS-vs-TFIDF dual stack | `tortoise/search_engine.py` + `tortoise/sparse.py` | C3 (verify-first only): query expansion behind knob | unit + integration (embedded TF-IDF fallback) |
| S10 Embedded mode | `retrieve.py` / `sparse.py` | C1–C4 must work under TF-IDF fallback | integration (embedded mini, CI) |
| S12 Graph writes | none (marks already written; no new props unless read-time recompute is too slow) | — | regression only |
| S27 Dataset fixture | `tests/fixtures/longmemeval_mini.json` | extended assertions: chunk evidence in reader context | integration |
| S28 Temporal/recency | `retrieve.py` TR path | C1 preserves TR item cap (tr_top_k=12) + time-ascending render + window filter — **pinned by test** | unit (existing TR tests + new ceiling test) |
| Reader contract (D6 #1540) | `run.py` → `reader.answer(context_hits=ret["context_points"])` | unchanged (context_points is the contract; content changes only) | integration |

**Explicitly NOT touched:** production `tortoise_fts_query` ranking internals (C2 lives in the eval layer); extractor prompts (S2/#1695 lane); ontology/kinds; the evidence denominator (D5 #1540 comparability).

---

## 6. Test Strategy

**Unit (pure functions, `tests/test_longmem_runner.py`):**
- `test_context_interleaves_points_and_chunks_by_rank`: chunk ranked above a point in the pool appears before that point in the context. **Replaces** `test_context_points_first_chunks_backfill` (points-first deliberately reversed).
- `test_context_item_cap_and_token_budget`: with a 60-item pool, context fills to `min(context_item_cap, budget-selected)` — item cap binds first when the pool is under the token budget; a >8k-token fixture shows the budget binding.
- `test_tr_context_keeps_item_cap`: TR question's context ≤ tr_top_k even with a budget walk (R5 flood control not undone).
- `test_context_reader_alignment_stays_exact`: `context_tokens == _estimate_tokens(render_context(...))` with interleaved ordering.
- `test_evidence_boost_promotes_marked_hits`: marked point at pool rank 25 surfaces in context after boost; unmarked at same rank do not.
- `test_evidence_boost_precision_guard_recomputed`: read-time `mark_for` recompute distinguishes verbatim (full boost) from source-only (reduced) — asserted on real annotated-hit shapes, not synthetic dicts.
- `test_evidence_boost_no_marked_point_displacement`: boosted chunks do not push marked points out of top-20.
- `test_boost_before_recall_metrics`: evidence@20 reported over the boosted pool; pre-boost ranking preserved for the ablation.
- `test_context_oversized_hit_skips_not_starves` (existing) stays green.
- `test_ranked_ids_populated`: `retrieve_for_question` fills `ranked_ids` / `evidence_turn_matches` (Task-0 gate).
- `test_max_chunks_per_session_three`: pool + context respect the 3/session cap; session recall stable (H4 regression).

**Integration (embedded FalkorDBLite mini fixture, CI-safe):**
- `test_mini_pipeline_end_to_end_mock` (existing) — update assertions: reader context now contains chunks + evidence-marked points; `context_tokens` higher than pre-fix on the mini fixture.
- `test_reader_context_contains_chunk_evidence`: a mini question whose evidence lives in a chunk → the chunk is in `context_points` (the H1 regression guard).
- `test_reader_evidence_recall_diagnostic`: `reader_evidence@k` ≈ `evidence@k` on the mini (pool→context drop → ~0).
- `test_tr_window_and_ceiling_combined`: TR + budget walk + window filter coexist.

**E2E / re-validation (docker lane, run protocol step 4→5) — the acceptance gate:**
- **Clean single-population run** with the **resume-quality gate** (reject checkpoint outcomes with `fts.count=0` / `session_recall@20=0` for resume — the deep-analysis's recommended health check that would have rejected 13 stale outcomes).
- Boost-enabled run (`TORTOISE_LME_EVIDENCE_BOOST=1`): evidence@20 ≥ 0.45, turn@20 ≥ 0.40, vacuity ≤ 0.10, session@20 ≥ 0.90, accuracy ≥ 0.74.
- **Ablation arm:** same clean run, boost OFF → report `reader_evidence@k` and pre/post `evidence@20` so fix attribution is visible (C1's effect on the context vs C2's on the pool).
- **Category-stratified subset** (TR + KU + MSR, ≥5 each) as a precondition for declaring shipped — the 50-Q subset is 100% single-session-user and cannot detect chunk-flood damage on other categories (verifier P1-5; R5's 9/18 TR refusal precedent).

**Regression:**
- Full `uv run pytest tests/ -m "not slow"`; `tests/test_longmem_reader_prompting.py` (cross-consumer of context shape) green; production search untouched — `test_search_engine.py` / `test_sparse.py` green.

---

## 7. Implementation Tasks

### Task 0: Diagnostic prerequisite — populate ranked_ids + evidence_turn_matches

**Intent:** The pilot's context composition is unreconstructable (0/50 populated) — C2 calibration and C4 need the ranked pool + evidence-turn matches persisted per question.

**Acceptance:** `retrieve_for_question` returns populated `ranked_ids` (pool order) and `evidence_turn_matches` (marked ids ∩ pool) on the mini fixture and the re-validation; the checkpoint stores them.

**Files:** Modify `tools/longmem_eval/retrieve.py`, `tools/longmem_eval/run.py` (outcome projection); Test `tests/test_longmem_runner.py` (`test_ranked_ids_populated`).

### Task 1: Rank-interleaved, budget+item-capped context assembly (C1)

**Intent:** Fix the dominant seam — chunks starved from the reader context by points-first + 20-item cap.

**Acceptance:** `_assemble_context` iterates the pool in rank order (points and chunks interleaved), bounded by `max_context_tokens` AND `context_item_cap`; TR keeps `tr_top_k`; `context_tokens == _estimate_tokens(render_context(...))` exactly; existing alignment/oversized tests green; module docstring updated (pool recall is an upper bound; `reader_evidence@k` is the reader surface).

**Files:** Modify `tools/longmem_eval/retrieve.py`, `tools/longmem_eval/run.py` (knob `TORTOISE_LME_CONTEXT_ITEMS`); Test `tests/test_longmem_runner.py`.

**Steps (TDD):** red tests → implement → green → commit via `commit-workflow`.

### Task 2: Evidence-mark boost with read-time mark recompute (C2)

**Intent:** Marked evidence points/chunks ranked 21–60 surface into the pool top-20 — the pool-metric mover.

**Acceptance:** `_apply_evidence_boost` applies rank offsets (verbatim ×1.5, source-only ×1.15) via read-time `mark_for` recompute; boost placed BEFORE `_recall_metrics`; knobs env-exposed; OFF by default in code; a synthetic test proves marked points are not displaced by boosted chunks; methodology records "evidence_recall@k measured over the boosted pool."

**Files:** Modify `tools/longmem_eval/retrieve.py`, `tools/longmem_eval/run.py`, `tools/longmem_eval/report.py`; Test `tests/test_longmem_runner.py`.

### Task 3: Reader-evidence diagnostic + report hygiene (C4)

**Intent:** Make the pool→context drop directly measurable; surface mark composition; populate ranked ids (moved to Task 0).

**Acceptance:** `reader_evidence@k` in `retrieve_for_question` + report `retrieval` block; `evidence_marks` aggregated in the report; methodology records C1/C2 knobs + updated `reader_context_format` string (rank-interleave).

**Files:** Modify `tools/longmem_eval/retrieve.py`, `tools/longmem_eval/report.py`, `tools/longmem_eval/run.py`; Test `tests/test_longmem_runner.py` (+ golden-shape test update).

### Task 4: Raise max_chunks_per_session to 3 (C5)

**Intent:** Stop capping out the evidence chunk (18 questions).

**Acceptance:** default cap 2→3; pool + context respect it; session recall stable; regression test green.

**Files:** Modify `tools/longmem_eval/retrieve.py`; Test `tests/test_longmem_runner.py`.

### Task 5: C3 verify-first diagnostic (rescoped)

**Intent:** Determine whether a genuine paraphrase gap exists on the clean population before building expansion.

**Acceptance:** measure FTS-empty + search_keys population on the clean run; if a real gap appears, implement `expand_query_with_search_keys` behind `TORTOISE_LME_QUERY_EXPAND` (max 12 terms, fail-open) + tests; else record the finding and do not ship.

**Files:** Modify `tortoise/sparse.py` (only if fired), `tools/longmem_eval/retrieve.py`; Test `tests/test_sparse.py` (only if fired).

### Task 6: Mini-fixture integration + regression pass

**Acceptance:** embedded mini E2E shows chunks + marked points in reader context; full CI green; `test_longmem_reader_prompting.py` green; production search tests untouched/green.

**Files:** Test `tests/test_longmem_runner.py`, `tests/test_longmem_reader_prompting.py`, `tests/test_search_engine.py`, `tests/test_sparse.py`.

### Task 7: 50-Q clean re-validation (the acceptance gate)

**Acceptance:** clean single-population run with the resume-quality gate + boost-enabled arm + boost-off ablation arm + category-stratified subset → evidence@20 ≥ 0.45 (boosted), `reader_evidence@k` reported (C1 effect), turn@20 ≥ 0.40, vacuity ≤ 0.10, session@20 ≥ 0.90, accuracy ≥ 0.74; methodology records all knobs. Report recorded alongside `pilot_20260825T222548Z.report.json`.

**Files:** none (run artifact) — record in the issue comment.

---

## 8. Complexity & Verification Checklist

| Domain | Rating | Rationale |
|---|---|---|
| Architecture | standard | Retrieval + context-assembly change, bounded scope, in-repo primitives only (no new deps) |
| Ontology | low | No schema/kind changes; marks already exist; read-time mark recompute avoids a graph change |
| UX | low | Reader context shape changes (points+chunks interleaved) — system prompt already handles chunk blocks; no new user-facing surface |

**Verification checklist (from the issue, re-based on the clean population):**
- [ ] retrieval recall (integration, docker lane): evidence@20 ≥ 0.45 (boosted), turn@20 ≥ 0.40 on the clean re-validation
- [ ] evidence marks (unit + integration): marks survive point construction and are retrievable; precision guard distinguishes mark types via read-time recompute
- [ ] regression (integration): session@20 ≥ 0.90; accuracy ≥ 0.74
- [ ] reader_evidence@20 (new): pool→context drop ≈ 0 after C1 — the honest reader-surface measure
- [ ] TR/KU/MSR stratified subset: no chunk-flood regressions on non-single-session categories

---

## 9. Coordination with #1695 (chain/kind changes)

- **#1695** (`feat(extraction): kinds-classification-later + deterministic chain enforcement`) changes the S2 extraction prompt — it changes WHAT points are written (kind assignment + chain rewiring), sequenced "pilot → improvement → pilot re-validation → 500-Q" (owner order 2026-08-25).
- **This issue (#1745)** is retrieval-side only: it changes how existing points/chunks/marks are assembled, ranked, and measured. No extractor prompt change, no payload contract change → **no code-level conflict** with #1695.
- **Interaction to coordinate:** both target the **50-Q pilot re-validation** gate (run protocol step 4 → step 5 500-Q). Recommended sequencing: (1) land #1745 first (retrieval fix measured against the SAME v2 extraction as the pilot baseline — clean isolation of the retrieval effect; both fixes share the same re-validation surface); (2) #1695 lands and its re-validation absorbs the retrieval fix; (3) the 500-Q V3 baseline (run protocol step 5) runs after BOTH and records the new retrieval knobs in methodology so the V4 comparison is not confounded.
- If #1695 lands first instead: the re-validation cannot attribute gains to either change alone — flag in the run record. Prefer #1745-first.
- **Pinned code-alignment point:** both touch `retrieve.py` consumers only if #1695 changes point content length/structure (it doesn't — same payload contract). No merge-conflict surface identified; verify at merge time via the shared test files.

## 10. Extra issues filed during scoping (not absorbed)

Per issue-scoping "file extra issues, don't silently absorb":
1. **Reader A1-clause calibration (over-abstention)** — the healthy population's entire fresh failure surface (4/4; `6f9b354f` abstained at ev@20=1.0). Deep-analysis finding #3 + C2 cluster. Highest *accuracy* lever on the clean population — separate from this retrieval issue.
2. **Answer-string evidence mark + metric re-baseline** — deep-analysis recommendation 4; the source-session-dominated denominator (472/479) understates answer availability; add a point-level answer-string mark analogous to `chunk_mark` and re-baseline evidence_recall.
3. **Resume-quality gate for the run protocol** — refuse to resume checkpoint outcomes with `fts.count=0` / `session_recall@20=0`; single-population re-validation discipline (would have rejected 13 stale outcomes). Run-protocol/capstone (#1549) concern.

---

## 11. Review Cycle Log

### problem-verify + solution-verify — Cycle 1
- Verifier A (problem diamond): H1 code-verified ✓; P1s: (1) baseline not decomposed by population (resumed/fresh blend contaminates targets), (2) C3's FTS-empty premise crash-confounded; P2s: accuracy-lever claim contradicts deep-analysis, missing reader-side framing, TR branch unpinned, C2 placement/default contradiction, budget-bind estimate overclaims, P3s: H4 count mismatch, RRF-score multiplier not implementable (scores dropped), research artifact missing adversarial frame; P4: evidence_marks already in checkpoint.
- Verifier B (Devil's Advocate, solution diamond): H1 starvation REAL ✓ (retrieve.py:616-622; simulation 0/20 chunks; context_point_count 20/50; tokens max 5099 < 8000); P1s: (1) evidence@20 pool-based → C1 cannot move it + C2 placement/default contradiction, (2) baseline contamination + no resume-quality gate, (3) C3 premise data-false (0/30 fresh FTS-empty), (4) C2 precision guard unimplementable from OR'd has_answer, (5) missing failure mode — reader flood/abstention amplification, KU supersession markers on chunks, MSR double-count; P2s: scores.rrf dropped, dedup cap lever untapped, ranked_ids 0/50 prerequisite; P3: docstring contract drift.
- **Controller action:** all P0=0. Fixed P1-1 (metric-vs-context reconciliation §4.6, C2 placement pinned before `_recall_metrics`, default contradiction resolved), P1-2 (baseline decomposition §1.2, clean-run + resume-quality gate in Task 7), P1-3 (C3 demoted to verify-first §4.3), P1-4 (read-time mark recompute §4.2), P1-5 (category-stratified gate + TR item cap + reader-abstention filed separately §4.5/§9). Incorporated P2s (item cap + saturation caveat §4.1, scores-threading re-spec, max_chunks 2→3 as C5, ranked_ids Task 0) and P3s (docstring note, H4 count fix, research adversarial frame row). Re-dispatch after fixes.
