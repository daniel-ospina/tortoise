---
title: "Cross-Encoder Reranking — Two-Stage Retrieval (Scope + Conditional Plan)"
type: decisions
issue: "#317"
date: 2026-08-13
status: scoped
method: issue-scoping v5.1 — double diamond + integrated verification gates (5 problem-verify cycles, 5 solution-verify cycles, coherence check)
domain: capability
doc_status: live
subjects.team: epistemic-team
created: 2026-08-13
---

# Scope — Cross-Encoder Reranking / Two-Stage Retrieval (#317)

**Issue:** daniel-ospina/tortoise#317 — "feat: cross-encoder reranking — two-stage retrieval"
**Status:** BLOCKED/GATED on #316 benchmark (2026-08-13 triage). This scope defines the decision gate, the conditional implementation slice, and the close criteria.
**Method:** issue-scoping v5.1 full double diamond — problem-diverge (2 agents) → problem-converge (2 agents, 82/82) → Phase 1.5 external research (6 queries post-dedup, persisted) → problem-verify (5 cycles) → Codebase Explorer → solution-diverge (3 approaches) → solution-converge → solution-verify (5 cycles) → coherence check → wiring check.
**Research brief:** `docs/research/2026-08-13-317-cross-encoder-reranking.md` (5 source-tagged findings, persisted via `_research_append.sh`).
**Gate-locked problem definition:** `/tmp/317-gate-definition-v5.md` (authoritative; supersession clauses for routing>0 noted inline).

---

## Confirmed Problem

The cross-encoder precision claim is **unfalsifiable as scoped** — no labeled retrieval relevance set exists in the repo (the only gold fixtures are extractor-domain, not retrieval relevance), #316's benchmark is deferred/OPEN and answers a different question (latency + precision/recall vs Neo4j/Supermemory/Honcho baselines, not a gold-labeled Tortoise relevance set), the corpus is small (hundreds of claims; the plan's own journey example returns 141 Points) where reranking is documented to barely help or hurt, latency is the binding constraint (300ms target; external CPU benchmarks place ms-marco-MiniLM-L-6-v2 rerank at ~210ms/top-10, ~410ms/top-20, ~0.8–2.5s/top-100 — no in-repo measurement exists; the gate must produce it), and the repo already ships an unevaluated graph-native rerank lever (GraphRanker, #25) that a text-only cross-encoder would compete with — so the scoping delivers a decision gate that can **actually fire**: gate numbers, a conditional implementation slice, and close criteria.

**Confidence: 82/82** (two independent converge agents; shared verified internal evidence + corroborated external sources; independence evidenced by a cross-agent citation correction of MS-Shift 2205.02870).

---

## Verification Gates

### problem-verify: 5 cycles, clean
| Cycle | Verifier A | Verifier B | Controller action |
|---|---|---|---|
| 1 | P0=0 P1=0 P2=2 | P0=0 P1=1 P2=1 | Fixed P1 (gate can't fire without labeled-judgment owner/construction path) → added Gate Input B step-0 + separate work item; fixed P2s (boundary phrasing, pre-anchored gate numbers) |
| 2 | P0=0 P1=1 P2=2 | P0=0 P1=1 P2=2 | Fixed P1-A (latency arithmetic: top-10 anchor vs top-100 slice — pool size became a gate decision variable), P1-B (Input B untimed — stall rule added) |
| 3 | P0=0 P1=3 P2=3 | P0=0 P1=3 P2=3 | Fixed P1s (hidden ONNX/qint8 dep; routing lever absent from slice; selection arithmetic; parked re-eval miscalibrated; un-owned measurement) |
| 4 | P0=0 P1=1 P2=4 | P0=0 P1=4 P2=4 | Fixed P1s (fp32 fallback re-listed top-10; p50/p95 dual basis; N undefined; A/B deadlock; 5%-at-n≈100 significance) |
| 5 | P0=0 P1=2 P2=2 | P0=0 P1=1 P2=3 | Residual P1s were patch-mechanics text inconsistencies (~100 vs n≈150 sweep; one parenthetical) — controller fixed deterministically, grep-verified (no semantic change) |

**Exit: no P0/P1 remain. GATE PASSES.**

### solution-verify: 5 cycles, clean
| Cycle | Verifier A | Verifier B | Controller action |
|---|---|---|---|
| 1 | P0=0 P1=5 P2=5 | P0=0 P1=3 P2=4 | Fixed P1s (pool survival at sdk.py:4644/4527; inter-request inference race; routing lever missing; eval composite confound; query-bound footgun; shipped-config pinning) |
| 2 | P0=0 P1=5 P2=9 | P0=0 P1=1 P2=6 | Fixed P1s (150ms timeout cap below gate's citation floor; pool-grid overflow; routing×statistic dilution; measurement chicken-egg; α-default vacating mutual exclusion) |
| 3 | P0=0 P1=2 P2=4 | P0=0 P1=2 P2=3 | Fixed P1s (routed-subset power breach → two-stage labeling; serve-path limit>gate_pool → str_limit=max(gate_pool, caller_limit)) |
| 4 | P0=0 P1=2 P2=2 | P0=0 P1=2 P2=4 | Fixed P1s (supersession of v5 n≈150 language; Task 7.5 carrier pinned verbatim) |
| 5 | P0=0 P1=0 P2=0 P3=4 | P0=0 P1=0 P2=2 P3=3 | **PASS** — both verifiers; P2/P3 documentation pins incorporated (horizon-binds default, exit-criterion set-language supersession, treatment-effect vs population-benefit labeling, σ_d conservative trigger, re-open re-selection re-binds contract) |

**Exit: no P0/P1 remain. GATE PASSES.**

### Coherence check (Phase 5.6): PASS
- Scheduled `qwen3.8-max` reviewer **unavailable** — API key blocked (401, 2 attempts, skill's 2-cycle cap). **`[QWEN-GATE]` note: Qwen coherence check could not converge — substituted a fresh-context reviewer with the identical prompt.**
- Substitute result: no P0; P1-1 (no explicit gate-runner task producing the gate record) and P1-2 (stage-2 query population unestablished) — both fixed with pre-registration amendments; confirmation pass: **PASS**, 5 P2 carrier pins (A–E) folded into the gate-runner spec + eval-set filing.

---

## Plan

### Part 0 — The Decision Gate (what #316 must show; what the gate record contains)

> **Mandate re-anchoring (documented deviation):** the gate is not strictly "#316 greenlight" — #316 alone cannot falsify the precision claim (it measures latency + baselines, not gold-labeled Tortoise relevance). The gate re-anchors on TWO inputs: latency headroom (A) AND a labeled eval-set precision baseline (B). Stated in this plan comment so the "GATED on #316" issue language doesn't conflict with the designed gate. E2E-8's "latency records, doesn't block" is superseded for this issue — latency is a hard greenlight/close condition; the record-and-block semantic applies only to flag-off operation (zero added latency).

**GATE INPUT A — latency headroom** (primary #316; in-repo fallback)
- **Decision variable = joint triple** `(pool ∈ {20,30,50}, quant ∈ {fp32,qint8}, routing ∈ {0%, 15-20%})`. Selection rule: argmax pool subject to `routing_weighted_rerank_cost(pool, quant, routing) @ measured p95 + RRF_p95 ≤ 300ms` AND `pool ≥ 2×K` (min meaningful pool 20). **Gate basis: p95 only.** Top-10 is NOT selection-valid. Among feasible configs prefer the largest pool; record the reason when < 50. Tiebreak: fewer new deps (fp32) → lower routing → lowest weighted cost.
- **Dependency declaration:** onnxruntime/optimum appear in NO manifest (verified); sentence-transformers is a non-base optional extra. qint8 selection ⇒ slice declares onnxruntime+optimum as an optional extra with build-time export; otherwise selection is constrained to fp32 rows.
- **#316 greenlight numbers:** #316 must show RRF-only p95 leaves ≥ the cross-encoder's cost at the gate-selected pool under 300ms total (per-row `(pool, quant, routing)` measured p95, Docker FalkorDB prod mode on stated hardware, p50/p95/p99). Time-box: #316 stalls past **2026-09-30** → in-repo measurement fallback (same ~150-query mix, concurrent load, Docker prod — NEVER FalkorDBLite; deployment mode + hardware recorded). A-fallback completion rule: verdict by **2026-11-30**, else A = FAILED → close (a).
- **Cap:** `TORTOISE_CE_TIMEOUT_MS` derived from the gate record = `ceil(1.5 × routed-conditional CE-stage p95)` of the selected triple — identical in harness and production. 150ms is a pre-selection placeholder that must NEVER serve (flag-on invalid before a gate record exists). Two-step protocol: measure uncapped → derive cap → re-measure under cap → record served p95. Cap-fired fraction > ~5% of CE executions → re-measure/raise trigger.

**GATE INPUT B — eval-set precision baseline** (separate work item → **amended into open issue #1144**, which owns labeled retrieval set construction and explicitly feeds #317)
- **Step 0 — provenance search** (owner: epistemic-team, by **2026-09-30**): org-wide search for existing labeled retrieval relevance sets; reuse if found (kappa ≥ 0.6 evidence or spot re-validation).
- **Two-stage labeling contract** (filed with #1144 before labeling):
  - **Stage 1:** n≈150 representative set (routing=0 case + latency mix + (b)/(e) diagnostic); kappa ≥ 0.6 via tools/kappa.py; JSONL schema pinned in #1144; judge_harness/min_signal reuse.
  - **Stage 2 (conditional):** triggered when Gate Input A's selected triple includes routing > 0 AND the (b)/(e) diagnostic passes. Protocol: sample ~750–1000 general-population queries, apply the calibrated threshold rule, label the routed subset (n_routed ≈ 150); label the non-routed complement OR record the full-set delta as estimated (share × routed delta, caveat recorded). **Population-feasibility check pre-registered:** if the query population doesn't exist (corpus is hundreds of claims), either (a) exhaustive ambiguous-tail labeling with power capped at available population — re-derive δ at 80% power from realized n_routed, re-pre-register pre-measurement; if no δ ≥ 5% achieves 80% power → fallback (b); or (b) routing>0 declared validation-infeasible → Gate Input A selection constrained to routing=0 rows (risk note: qint8-selection and close-(a) probabilities rise — the gate working on evidence).
- **Pre-registered statistic (ONE rule):** greenlight iff cross-encoder precision@10 delta ≥ 5% vs BOTH baselines (RRF-only AND GraphRanker — measured on the same set, paired, stricter outcome binds) AND one-sided p < 0.05 (90% bootstrapped CI excluding 0). Routing=0 → full-set statistic at n≈150, ~80% power (σ_d ≤ 0.25, mid-course tail check on stage-1 routed labels). Routing>0 → routed-subset statistic at n_routed≈150 (conjunction power noted in the gate record). Raw ≥5% failing significance → re-measure (n_routed 150→200 ⇒ ~1000–1333 queries) or park.
- **Conflict rule (pre-selected, standing):** for routing>0, greenlight = routed-subset statistic passes; full-set served-config delta (expected < 5% by design — dilution ~0.15–0.20 × routed delta) is recorded as the **shipped benefit** (label: "population-level benefit (diluted)" distinct from "routed-subset treatment effect (the gate decision)"). Option (a) (re-select higher routing share) REMOVED as unexecutable (routing > 20% outside the grid; drift cap at 20%; 15→20% moves the delta only ~0.75→1.0%).
- **Supersession (explicit):** for routing>0 selections, the two-stage labeling contract SUPERSEDES the gate definition's "n ≈ 150" sizing, the close-criteria "on the n≈150 set" language, the "extend to n≈200" re-measure path, and the direct exit criterion's "on the eval set" phrasing (for routing>0, the served-config head-to-head IS the routed-subset statistic). For routing=0, the full-set contract applies unchanged. No two binding statements of the decision statistic survive.
- **Stall/parked state:** B not verdict-ready when A passes → **"deferred-verdict (parked)"**; re-eval = max(eval-set issue filed + 4 weeks, 2026-10-31 + 4 weeks), then every 8 weeks; progress = ≥25 labels or one completed milestone per cycle; absolute horizon **2027-03-31** (binds; milestone cadence must fit within it; extension requires explicit gate-record amendment at filing). Terminal close (iii): "deferred-verdict (evidence window exhausted)"; routing>0 mid-stage-2 resolves as NOT-VALIDATED (flag-off, recorded) — never a dangling provisional.

### Part 1 — Implementation Slice (CONDITIONAL on both gate inputs passing)

> **Gate-runner task (deliverable = the machine-readable gate record):** compose the joint-triple decision matrix from the latency tool's output rows, apply selection + tiebreak + below-50 counterfactual (measured cost thresholds at which pool=50 WOULD have been feasible — from the decision matrix, not post-hoc rationalization), consume the four input states (A rows, B verdict state, population-feasibility verdict, (b)/(e) diagnostic result) in the v5 precedence order (A → (b)/(e) → statistic → verdict), fire greenlight / close (a)-(e) / deferred-verdict, materialize the record as code-level constants (pool, quant, routing, α, cap, re-eval anchors, parked owner epistemic-team). All Task 4.x integration tasks are conditional on record = greenlight; Task 4.5 activated iff record.routing > 0 (implementation may proceed on provisional; flag-on requires routing-final). Re-runnable at each re-eval anchor and under the re-open rule (a re-open re-selection that changes routing re-binds the precision contract; flag-on does not persist across re-selection).

**Approach: Approach 2 — GraphRanker-compatible pluggable ranker + pipeline stage** (chosen over Approach 1 pipeline-stage and Approach 3 sidecar — see Rejected Alternatives):

| Task | File | What |
|---|---|---|
| 1 | `tortoise/cross_encoder.py` (new) | `CrossEncoderModel` lazy singleton — embeddings.py parity (worker-thread load, `_LOAD_TIMEOUT_S=45`, negative cache, HF_HUB_OFFLINE degrade, `_reset()` hook); loads `sentence_transformers.CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")`, version-tolerant max_length/max_seq_length (v5.4 rename); `score(query, texts)` via single `_infer()` entry point with a CLASS-LEVEL inference lock (tokenizer not thread-safe — HF #993); model resolved before lock (leaf-only); load path never touches the inference lock; optional qint8 path gated on `TORTOISE_CE_QUANT` (lazy onnxruntime/optimum import, fp32 fallback on ImportError) |
| 2 | `tortoise/search_engine.py` | `rerank_cross_encoder(results, query)` stage — atomic pool scoring (all-or-nothing; partial scores discarded on timeout — never a torn list), breaker keyed `"rerank"` (`_CircuitBreaker` pattern), returns input UNTOUCHED (no added keys) on any degrade/no-query/pool<2/breaker-OPEN; `_should_route()` deterministic RRF-gap helper (route-on-small-gap, RRF scores only, zero model calls) |
| 3 | `tortoise/ranking.py` | `CrossEncoderRanker` — exact GraphRanker contract `rerank(results, *, entity_type="point")`; query bound at construction (documented per-query construction; `rerank()` asserts current query matches bound query → ValueError on mismatch, never silent stale scoring); optional `base_ranker=` composite (α env-tunable, **default UNSET** — strictly opt-in; composition order: RRF → CE rerank of gate pool → α-weighted blend of CE vs GraphRanker/EP scores → top-K; promoted-to-default re-fires latency + head-to-head) |
| 4 | `tortoise/sdk.py` | `TORTOISE_CROSS_ENCODER_ENABLED` (via `_env_bool`, backup_config.py:69); per-call `cross_encoder_enabled: bool | None = None`; pool plumbing — flag-on `str_limit = max(gate_pool, caller_limit)` (sdk.py:4527), truncation `result_ids[:pool]` when CE active (after ALL filters, ~4649), output always = caller limit; RRF-order tail documented (positions > gate_pool; not a stacking violation); **mutual exclusion**: flag-on + order_by=graph/confidence without explicit composite → NO auto-CE (loud log); suggest_entry_points passes `ce_enabled=False` explicitly; flag-off path byte-identical (str_limit = limit*2, truncation [:limit]) |
| 4.5 | `tortoise/search_engine.py` | **Routing lever (conditional on record.routing > 0):** `_should_route` threshold calibrated on the eval-set dev split (independent of precision-test queries) to land 15-20%; determinism test (same query → same route); drift rule (>20% → re-select triple or cap routing); low-side rule (realized fraction below selected row → re-open) |
| 5 | `tortoise/hosted_api.py`, `Dockerfile.hosted`, `Dockerfile.selfhost`, `entrypoint.sh` | Hosted: bake cross-encoder into Layer-1 model cache (`/app/model/models--cross-encoder--ms-marco-MiniLM-L-6-v2`), conditional build-time check, `_lifespan` pre-warm `CrossEncoderModel.get(load_timeout=300.0)` ONLY when flag on; entrypoint CE cache check conditional on the flag (never unconditional FATAL on a flag-off feature). Selfhost: on-demand download via EmbeddingModel pattern (load timeout, negative cache, retry), flag-off default = no download, load failure = RRF degrade + breaker |
| 6 | `tools/eval_rerank_head_to_head.py`, `tools/measure_rerank_latency.py` (new) | Head-to-head harness: 3 arms (RRF-only = relevance+flag-forced-off via per-call kwarg, with CE-singleton-never-instantiated assertion; GraphRanker = graph+GraphRanker(proj); CE = served config incl. served cap), (b)/(e) diagnostic as gated pre-step (close-path files retrieval-recall fix separately), pre-registered statistic, artifact JSONL → `eval_artifacts/`. Latency tool: refuses FalkorDBLite, Docker-prod + pinned hardware (reference #1146), disjoint partition (RRF_p95 per pool/caller-limit row — shaped by internal 500ms per-strategy timeout, stated; + filter_enrichment_p95 at pool; + rerank_only_p95 routed-conditional) with the gate check = measured full-mix total-search-p95 per row ≤ 300ms; CE-input content truncation contract pinned (max_length 512, char budget, where it lands in the partition) |
| 7 | `docs/scoping/2026-08-13-317-cross-encoder-scoping.md`, `docs/00_index.md` | Scope doc registration; gate record + E2E-8 reconciliation + mandate re-anchoring + supersession line; Gate Input B contract amendment forwarded to #1144 |
| 8 | Final verification | pytest full suite; golden-output regression (flag-off byte-identical, batch size pinned); eval artifact produced + linked in gate record before any flag-default flip; slice-level latency re-verification under cap (p95 bust → re-open) |

**Runtime prerequisites:** model ~90MB baked at Docker build (hosted) / on-demand (selfhost); env vars `TORTOISE_CROSS_ENCODER_ENABLED` (default false), `TORTOISE_CE_POOL` (20|30|50, validated), `TORTOISE_CE_ALPHA` (unset), `TORTOISE_CE_BATCH_SIZE` (32), `TORTOISE_CE_MAX_LENGTH` (512), `TORTOISE_CE_TIMEOUT_MS` (derived from gate record), `TORTOISE_CE_QUANT` (fp32 default), `TORTOISE_CE_ROUTING_THRESHOLD` (unset = disabled).

**Acceptance criteria (gate-aligned):** (a) flag-off byte-identical (golden regression); (b) degrade contract (model missing/timeout/breaker-open → RRF-only identical, no torn list); (c) eval artifact produced with the pre-registered statistic + served-config match; (d) two-stage pipeline at gate-selected pool, output = caller limit, never sub-floor; (e) explicit stacking (mutual exclusion holds; composite opt-in; never silent double-rerank); (f) model-host parity (embeddings.py lifecycle + Docker bake + flag-gated pre-warm); (g) deps (zero new for fp32; qint8 declared only on selection); (h) routing deterministic (if selected); (i) **direct exit criterion: flag default stays False unless the recorded head-to-head shows ≥5% vs BOTH baselines AND one-sided p<0.05 — feature available-but-unvalidated, never silently shipped.**

### Part 2 — Close Criteria (if the gate fails)

- (a) No latency headroom at any pool ≥ 20 (from #316 + fallback measurement; A-fallback failure by 2026-11-30 → A = FAILED).
- (b) Eval-set diagnostic: baseline top-5 already ≥ 60% (no ordering headroom).
- (c)+(d) merged pre-registered statistic: cross-encoder fails to beat BOTH baselines by ≥ 5% with significance (routing=0 full set; routing>0 routed subset at re-pre-registered power).
- (e) Diagnostic shows correct docs MISSING from top-50 in > 10% of queries (retrieval recall failure — reranking cannot fix first-stage recall; file the retrieval-recall fix as a separate issue).
- **(iii) deferred-verdict close** (outcome class, distinct from falsified closes): terminal rule at 2nd no-progress re-eval / 2027-03-31 horizon — closed as "deferred-verdict (evidence window exhausted)"; eval-set work continues standalone in #1144 (unblocks GraphRanker/StateRanker evaluation, #7701); corpus-growth revisit trigger attaches (reopen when corpus > ~1,000 Points — top-K < ~10% of corpus — or a consumer demonstrates a quantified ordering-driven quality gap).
- **RETAIN the labeled eval set regardless** (highest-value outcome — it unblocks evaluating the actual differentiator, GraphRanker/StateRanker, #7701); retention owner = epistemic-team.

---

## Clarifications

*(from issue-pre round — 2026-08-13)*

No clarifying questions needed — all human-judgment decisions were already specified in the issue body (model, flag name, targets, gate). Pass B (deferred to research) seeded Phase 1.5: rerank-vs-GraphRanker composition, gate numbers #316 must show, candidate-pool sizing, model-missing failure mode, precision measurement method.

### Deferred to Research (Pass B — answered in Phase 1.5)
- Composition order of cross-encoder vs existing GraphRanker (#25) — answered: two-baseline comparison + mutual exclusion + opt-in composite.
- What #316 must show (numbers) — answered: per-row (pool, quant, routing) p95 headroom under 300ms.
- Candidate-pool sizing — answered: gate decision variable {20,30,50}, min-pool 20, 50-floor preference.
- Model-missing failure mode — answered: degrade contract (byte-identical RRF, never torn).
- Precision measurement method — answered: two-stage labeled eval contract, pre-registered statistic.

---

### Axis Research

> **Axes:** Architecture=medium (new pipeline stage + latency integration), Library-deps=TRIGGERED (new model dep cross-encoder/ms-marco-MiniLM-L-6-v2), UX=low, Ontology=low (justified skip — SDK/CLI surface, no UI, no schema change; feature flag is config).
> **Codebase-first precedent scan:** model-loading singleton (tortoise/embeddings.py — 1 strong precedent), flag pattern (_env_bool backup_config.py:69), breaker pattern (search_engine.py), GraphRanker contract (ranking.py) → query weights lightened per protocol.
> **Dedup vs PRIOR_RESEARCH:** docs/research/2026-08-03-hybrid-search.md covered "~100x slower" + two-stage pattern at brief granularity (⚠️ single-source tag); NOT covered at sufficient granularity: model-specific CPU latency, precision gains at top-10, selective reranking, late-interaction alternatives, model packaging — fired fresh queries. Deduplicated questions never counted toward the cap.
> **Post-dedup queries: 6** (≤ 8 Fast cap): Perplexity ×2 (production latency-budget failures; two-stage canonical), Exa ×4 (ms-marco-MiniLM-L-6-v2 CPU latency; when-reranking-hurts; ColBERT comparison; sentence-transformers production pitfalls). Persisted as 5 source-tagged findings in `docs/research/2026-08-13-317-cross-encoder-reranking.md` (canonical ×1, pitfalls ×3, competitor ×1).

**Findings (persisted, with provenance):**
- **Canonical — two-stage best practices** (Hybrid Search Book; superteams.ai; devtechtools; Pinecone; Vespa docs; TREC DL): candidate pool sweet spot 50–100 (gains plateau ~200; "cap at 50" — 72technologies); RRF BEFORE rerank; truncate at index time (latency is bimodal — p50 vs p99 driven by doc length); cross-encoder score is a LOGIT not a probability (ranking only, never threshold); weighted fusion of first-stage + reranker often beats reranker alone; max_length 512 / chunk ≤ 1500 chars.
- **Pitfalls — model latency** (Metarank; DadOps; OneUptime; temsa ONNX qint8; tianpan.co; towardsdatascience queue sim): ms-marco-MiniLM-L-6-v2 (22.7M params, ~80–90MB): GPU 12.3ms/1, 58.7ms/10, 740ms/100; CPU ~210ms/10, ~410ms/20, ~980ms/50, ~2.1s/100; CPU batch=1 2500ms/100; ONNX qint8 ~30–40% faster (210ms/20, 578ms/50); production case study: +4 nDCG@5 offline but p99 +700ms over SLO → perceived quality DOWN; QPS collapse (p99.9 > 21s @ 40 QPS). **Top-100 CPU rerank violates 300ms by 3–8×.**
- **Pitfalls — when reranking HURTS** (bigdataboutique; folarin.dev; 72technologies; adaptiverecall; theneuralbase; arXiv 2411.11767): corpora < ~1,000 docs / recall@3 > 0.9 → skip ("shuffling cards that are all correct"); value window = recall@50 high but ordering poor (correct doc buried rank 20–30); practitioner diagnostic: label 100 queries — correct in top-50 > 90% but top-5 < 60% ⇒ reranker moves the needle; missing from top-50 ⇒ fix retrieval first; arXiv 2411.11767 cited PRECISELY (candidate-count scaling + full-retrieval degradation — NOT the small-corpus inference, which rests on the marginal-recall argument).
- **Competitor — late-interaction ColBERT** (datarekha; thread-transfer; hybridsearchbook; arXiv 2004.12832 / 2302.06589): ~170× faster at near-parity MRR; sub-linear rerank cost; BUT index 50–100× larger (ColBERTv2 residual → 5–10×), not worth it < ~100K chunks — wrong for small corpora; selective/conditional reranking (15–20% of queries) cuts latency 57% with <2% loss (clawrxiv 2604.01082); LLM listwise = quality ceiling but 2–3 orders slower.
- **Pitfalls — sentence-transformers production** (HF #3078; HF #993; sbert efficiency docs; groktocrawl ADR-0034): device not pushed until predict(); tokenizer NOT thread-safe (ThreadPool → "Already borrowed"; GIL); model init ~2s; qint8 via Optimum ~30–40% CPU speedup; lazy-init singleton + HF_HUB_OFFLINE + Docker pre-download is the house pattern (embeddings.py); fallback strategy required when reranker unavailable — flag-off must never mean pipeline re-tuned for reranker-on (tianpan.co trap).

### Integration Docs

| Dep | Version | Status |
|---|---|---|
| `sentence-transformers` | `>=3,<6` | **Already declared** in `[project.optional-dependencies] embeddings` (pyproject.toml:37, verified). `CrossEncoder` ships in the same package — **zero new deps for fp32**. Installed in hosted image via existing `.[embeddings]` bake; NOT in default dev env → CE degrades exactly like embeddings. |
| `onnxruntime` / `optimum` | n/a | **0 occurrences in any manifest** (verified). Conditional: ONLY if the gate selects `quant_mode=qint8`; then optional extra `[cross-encoder-quant]` + documented build-time export (fp32 → qint8 via optimum), version-compatible with the installed sentence-transformers major; measurement must measure the qint8 path it selects. |
| `scipy` | optional | Paired one-sided test in the harness when available; bootstrap fallback (mirrors tools/kappa.py) — zero hard deps. |

**API-surface findings — `sentence_transformers.CrossEncoder` (3.x–5.x):**
- Constructor `CrossEncoder(model_name, max_length=512)`; **device="cpu" explicit** (CPU is the deployment profile; gate basis is CPU p95). ⚠️ v5.4+ renamed `max_length` → `max_seq_length` (softly-breaking, deprecation warning) — the `>=3,<6` range spans both; CrossEncoderModel is version-tolerant (try max_length → TypeError → max_seq_length; accept-and-document 5.x warning).
- `predict(pairs: list[list[str]], batch_size=32, show_progress_bar=False)` → **logits** (ms-marco family; higher = more relevant; logit ≠ probability — ranking only). Use `predict` directly (not `rank`) for explicit pool control. Batch size affects cost (gate's cited numbers are batch-dependent — measurement protocol records it).
- Output scale: logits unbounded vs GraphRanker 0–1 weighted sums → **min-max normalize within the pool** before any α-blend (`ranking._min_max_normalize` precedent; degenerate all-equal → midpoint guard, stable order).
- Import cost: heavy (torch) — only ever inside the worker-thread load, never module import. Cache: HF_HOME/SENTENCE_TRANSFORMERS_HOME → /app/model under HF_HUB_OFFLINE=1 (hosted).

---

## Rejected Alternatives

**Problem framings (Phase 2):**
- **Framing 1 (precision-lever selection):** rejected as the definition — presumes levers comparable; #7701 (EP confidence vs relevance) unanswered. Absorbed as the gate's first action (RRF-vs-GraphRanker comparison before any cross-encoder earns the latency budget).
- **Framing 3 (recall vs ordering):** rejected as the definition — diagnostic lens, largely pre-answered (coverage, given corpus size). Absorbed into the (b)/(e) diagnostic.
- **Framing 4 (original, conditional):** rejected — every condition provably unmet (benchmark deferred, no eval set, latency binding, corpus small). The original's escape hatch was the gate; the gate is now the deliverable.
- **"Skip and close outright":** rejected as the definition — closing without measurement forecloses the gate rather than firing it; measurement also unblocks the real differentiator (GraphRanker/StateRanker, #7701). Accepted as the LIKELY outcome (close criteria).
- **Fix root-cause principle applied:** the issue's symptom was "add cross-encoder"; the root cause is unfalsifiable precision claims + an unevaluated existing lever. The scope targets measurement + gate, not the symptom.

**Solution approaches (Phase 5):**
- **Approach 1 — pipeline-stage rerank ("CE as a first-class search stage"):** rejected as primary — CE logic across 3 order_by branches in the hottest path; sequential-only stacking with no tunable blend; heavier harness axes; uniform application would contaminate the GraphRanker baseline (never-silent double-rerank). **Would have been better if** the gate mandated a fixed unconditional rerank with no GraphRanker comparison. **Adopted:** the stage function lives in search_engine.py (the gate's "two-stage pipeline" language).
- **Approach 3 — rerank sidecar service:** rejected — the gate explicitly mandates embeddings.py host-parity (in-process lazy singleton); a sidecar violates it (needs sign-off we don't have); adds network dep + auth + ops to a self-contained search path; breaks selfhost parity. **Would have been better if** in-repo measurement showed in-process cannot hold p95 ≤ 300ms on stated hardware — documented escalation path, not a silent choice.
- **On-demand model download (no Docker bake):** rejected for hosted (HF_HUB_OFFLINE regime requires build-time bake + pre-warm); kept for selfhost (on-demand per embeddings.py pattern).
- **MCP-layer feature flag:** rejected — hosted_api /v1/search exposes only q+limit; an MCP-layer flag would silently never fire in production. Flag is SDK-layer (env) with per-call override.

---

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| #316 benchmark (Gate Input A primary source) | External dependency | #316 (open) — gate time-box 2026-09-30 + in-repo fallback | ✅ |
| #1146 FalkorDB image pin + resource limits (reproducible latency measurement) | External dependency | #1146 (open) — referenced by latency tool protocol | ✅ |
| #1144 labeled retrieval set (Gate Input B — stage-1 n≈150 + conditional stage-2 extension) | External dependency | #1144 (open) — two-stage contract amended in via comment | ✅ |
| `search_engine.py` rerank stage + `"rerank"` breaker + `_should_route` | Code | Slice Tasks 2, 4.5 | ✅ |
| `sdk.py` flag + pool plumbing + step-9 wire-up + mutual exclusion | Code | Slice Task 4 | ✅ |
| `ranking.py` CrossEncoderRanker + opt-in composite | Code | Slice Task 3 | ✅ |
| `cross_encoder.py` CrossEncoderModel singleton + inference lock | Code (new) | Slice Task 1 | ✅ |
| `mcp_server.py` tortoise_search passthrough | Integration | Env inheritance (SDK-layer); no CE param; composites SDK-only — documented | ✅ |
| `hosted_api.py` /v1/search (q+limit, ≤100) + `_lifespan` pre-warm | Code | Slice Task 5; flag-on caller limit ≤ 100 | ✅ |
| `Dockerfile.hosted` bake + `entrypoint.sh` conditional check + `Dockerfile.selfhost` on-demand | Infra | Slice Task 5 | ✅ |
| `.env.example` CE env vars + pool validation | Config | Slice Tasks 1, 7 | ✅ |
| eval harness + latency tool + `eval_artifacts/` | Tooling (new) | Slice Task 6; JSONL schema pinned in #1144 | ✅ |
| `tools/kappa.py`, `tools/min_signal.py`, `judge_harness.py` reuse | Tooling | Slice Tasks 6, 7 | ✅ |
| how-to-use-tortoise skill (search section — CE note) | Docs | Deferred (documented): skill update was hybrid-search Phase 2 scope; CE note ships when/if the flag ships | ⚠️ documented-deferred |
| docs/00_index.md registration + gate record | Docs | Slice Task 7 | ✅ |
| #7701 (EP confidence vs relevance) open question | Related | #1144 EP-annotation evaluation + eval-set retention | ✅ |

**<HARD-GATE>** — no wiring gaps: all touch points have an owner (issue, slice task, or documented deferral). **PASSES.**

---

## Review Cycle Log

- **problem-verify:** 5 cycles, 10 fresh-context verifier dispatches, 1 controller-fixed P1 per cycle (cycles 1–4) + deterministic text-sweep fixes (cycle 5). All P1s resolved; exit clean.
- **solution-verify:** 5 cycles, 10 fresh-context verifier dispatches. Cycle 1: 8 P1s → fixed; Cycle 2: 6 P1s → fixed; Cycle 3: 4 P1s → fixed; Cycle 4: 4 P1s (carrier/documentation) → fixed; Cycle 5: PASS (both verifiers, P2/P3-only).
- **Coherence check (Phase 5.6):** `[QWEN-GATE]` — qwen3.8-max unavailable (API key blocked, 401 ×2, skill 2-cycle cap → "could not converge" surfaced per skill). Substitute fresh-context reviewer (identical prompt): no P0; 2 P1s fixed (gate-runner task; stage-2 population feasibility) + confirmation pass clean; 5 P2 carrier pins folded into the gate-runner spec + #1144 filing.
- **Parallel Review Gates (Phase 7):** satisfied by the verification gates + coherence review acting as the fresh-context review cycle (per orchestration directive) — 22 fresh-context dispatches across 12 review passes. Convergence achieved: cycles converged from structural defects → arithmetic consistency → carrier/documentation pins → clean.

---

## Complexity

| Domain | Rating | Rationale |
|---|---|---|
| UX | low | SDK/CLI only; no UI. Flag-off default means zero user-visible change until ship. |
| Ontology | low | No new entities/fields; feature flag is config; result schema unchanged (CE annotation adds a breakdown key on flag-on only). |
| Architecture | **high** | New pipeline stage in the hottest search path (sdk.py step 9), pool-flow restructuring (str_limit/truncation), breaker integration, GraphRanker composition semantics, model lifecycle parity, Docker bake, eval+latency tooling, concurrency (inference lock). |
| Library-deps | **triggered** | New model dependency (cross-encoder/ms-marco-MiniLM-L-6-v2 via existing sentence-transformers — zero new deps fp32; onnxruntime/optimum conditional qint8). |
| Data | low | No migration; EP annotation/content fetch scale with pool size (measured in the latency partition). |
| Testing | **high** | Golden-output regression, degrade-contract suite, breaker tests, harness statistic tests, model-injection tests (sys.modules pattern), routing determinism. |
| Complexity (overall tier) | **standard** | Issue body + scope agree: standard (research + integration). |

---

*Scoped 2026-08-13 via issue-scoping v5.1. Gate-locked artifacts: `docs/research/2026-08-13-317-cross-encoder-reranking.md` (research brief), gate definition v5 (in this doc Part 0), plan comment on #317.*
