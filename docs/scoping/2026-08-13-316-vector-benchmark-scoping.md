---
title: "Scoping: #316 — Verify and Benchmark FalkorDB Vector Index Performance"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

# Scoping: #316 — Verify and Benchmark FalkorDB Vector Index Performance

**Date:** 2026-08-13 · **Tier:** standard · **Skill:** issue-scoping v5.1 (double diamond + verify gates)
**Issue:** daniel-ospina/tortoise#316 · **Consumer gate:** #317 (cross-encoder reranking)
**Research brief:** docs/research/2026-08-13-316-vector-benchmark.md

---

## Confirmed Problem

Characterize the hybrid retrieval path's latency on a **prod-parity (Docker/HNSW) environment** with documented host/container resource specs — measuring all 4 strategies (FTS, vector, hybrid RRF, TF-IDF fallback) **in isolation AND under the parallel degradation chain's 500ms collective cap** (right-censored; internal elapsed timers for true completion), PLUS the **full end-to-end budget composition** (in-path embedding encode, RRF fusion + post-processing, EP annotation, relationships, entity fetch, serialization) — in **steady-state after warm-up with cold-start measured separately** — evaluating the **300ms E2E-8 target for correctness AND achievability** against an operationalized "typical query load" — documenting the **dominant latency strategy + optimization recommendation**, so the **#317 cross-encoder gating decision (latency headroom) is made on evidence**. A **corpus-size scaling check** (current + 10K/100K synthetic with EP structure — NANDs/supersedes/mitigations) identifies at what corpus size each strategy exceeds its budget and the degradation cap starts dropping strategies. Corpus provenance is explicit (real event-log replay where available; synthetic seeded with EP structure otherwise — synthetic numbers indicative, not prod-equivalent).

**Precision/recall@K and competitive baselines (Neo4j/Supermemory/Honcho) are explicitly DEFERRED** to a separate quality study (tracking issue filed) gated on real query logs + a labeled retrieval set — no such set exists (bench_gold.py is extractor eval), and cross-vendor P/R@K is not comparable (Points+operators vs messages vs conclusions). F1's quality question (does reranking pay for itself) is answered by **#317's internal precision@10 > 5% criterion** — this issue supplies only the latency side.

### Why This Framing (vs original)

The original issue's per-strategy targets (FTS<50ms, vector<100ms, hybrid<200ms, TF-IDF<500ms) encode a methodology error: the 3 strategies run **in parallel under a 500ms collective cap** (degradation_chain, ThreadPoolExecutor + `as_completed(timeout=0.5)`), so "hybrid RRF < 200ms" ≈ max(strategy) + microseconds of fusion — not an independent quantity. The 300ms end-to-end budget is dominated by **non-strategy overhead** (in-path embedding encode at sdk.py:4515, EP annotation, relationships, entity fetch, serialization) — none in the original "What to Measure". The competitive baseline is apples-to-oranges (different indexed units/hardware/corpora). The gated consumer #317 needs a **latency-headroom number**, not a cross-vendor P/R@K study.

### Falsification Check

This definition is wrong if: (1) a labeled retrieval set or real query logs already exist in hosted deploys (P/R@K feasible now — deferral wrong); (2) Docker/HNSW prod-parity env is unavailable (embedded FalkorDBLite brute-force numbers can REVERSE the #317 decision); (3) the live corpus is already ≥100K vectors (scaling premise fails — pure perf characterization); (4) #317 is descoped (no hard consumer). Verified against #317's body: gate IS latency-primary ("Do NOT implement until benchmark confirms Phase 0 latency budget").

**Confidence: 80/100** (converge agents: 80/80; every structural claim code-verified; residual: hosted corpus size + Docker availability unverifiable from repo).

---

## Verification Gates

### problem-verify — 1 cycle, clean
- Verifier A: P0=0, P1=0, P2=2, P3=2, P4=1
- Verifier B: P0=0, P1=0, P2=2, P3=3, P4=2
- Controller action: No P0/P1 → pass-through per gate mechanics. Incorporated all P2+ findings: corpus-size sweep restored, corpus-provenance clause, cold/steady split, host-spec documentation, 500ms right-censoring note + internal elapsed timers, operationalized query load, isolation pass restored, deferred quality study filed as tracking issue, F1 disposition documented, budget composition extended with RRF fusion/post-processing.
- **Gate: PASS** (no re-launch needed — P2+ only)

### solution-verify — 5 cycles, clean
- **Cycle 1:** A P1=1 (circuit-breaker hygiene), B P1=1 (driver-level 500ms timeout still censors true completion). Controller: fixed both → re-dispatch.
- **Cycle 2:** A P1=1 (production-column vs fail-fast contradiction), B P1=0. Controller: fixed (timeout-kill whitelisting, breaker-open bucketing, zombie settle gap, index verification re-scope) → re-dispatch.
- **Cycle 3:** A P1=1 (decision-rule collapse), B P1=1 (censored-tail capture via return-tuples discarded). Controller: fixed (two-column decision split, shared mutable trace sink) → re-dispatch.
- **Cycle 4:** A P1=2, B P1=4 (sink value semantics + driver capping authority; elevated timeout on all 3 surfaces; MCP surface sink reach; slow-arm mechanism; breaker-open biased verdicts; uncensored provenance). Controller: fixed all 6 → re-dispatch.
- **Cycle 5:** A P1=0 P2=3, B P1=0 P2=3. Controller: incorporated all P2+ (TF-IDF slow-arm trigger, probe contingency ladder, capped-set membership, timeout-threading 4 mechanisms, join-hang watchdog, uncensored E2E surface pin, verdict-band column mapping, synthetic text skew, MCP arg pinning, contextvar single-mechanism, digest pinning, 11 boundary enumeration, warm-up cap, non-Point unindexed kinds note).
- **Gate: PASS** (both verifiers returned no P0/P1)

### coherence check (Qwen 3.8-Max substitute — provider blocked) — 2 cycles
- Qwen provider API key blocked (401) — [QWEN-GATE]: "Qwen coherence check could not converge (provider unavailable)" → fresh-context coherence verifier substituted.
- **Cycle 1:** P1=2 (cold-start dropped from Phase 5; env/provenance not wired into run_report), P2=6. Controller: fixed both P1s + all P2s → re-run.
- **Cycle 2:** P1 residual=1 (E2E-path cold-start scope — first-N MCP queries after fresh boot), P2=6. Controller: applied fix (E2E cold-start record) + incorporated P2s (embedding model in env block, index-param application on synthetic corpora, real-embedding-seeded synthetic vectors, verdict-column sample floor ≥100, measurement-order pre-registration, verdict corpus-scoping + min-floor fallback, warm-up non-convergence handling).
- **Disposition: converged with controller-applied fix; no residual P1 in final plan.**

---

## Plan

**Approach: B-core hybrid** — default-off trace instrumentation as the measurement core, standalone report runner, conditional EventLog+fold replay corpus provider. 6 tasks.

### Task 1 — Measurement core (`benchmarks/bench_core.py`, `benchmarks/query_mix.json`)
Pure, deterministic latency math + pinned query mix. No measurement logic inside pytest or the report runner.
- Percentiles (p50/p95/p99 + mean/min/max/count), right-censor helpers, warm-up sampler, host/container spec capture, markdown renderer.
- **Pinned protocol:** ≥50 distinct queries (IR convention ~50 information needs, Manning); per-query sample counts; per-tier samples 100/50/25 (current/10K/100K) with per-arm wall-clock budgets (100K fallback/brute-force arms may be single-digit samples — documented deviation with p99-CI note); warm-up: discard first N + rolling-window CV<10% over last W, max-iterations cap (non-convergence → tier marked low-confidence in provenance, never silent); sequential AND concurrent sweeps; cold-start = N=1 fresh-process rep measured separately.
- Two-column protocol: **censored** (production behavior, pristine path, driver cap) vs **elevated-cap** (uncensored true completion) — unpaired independent runs, per-column provenance headers, per-column sample counts.
- query_mix.json: weighted specs (strategy-class × entity-type × limit ∈ {5,10,20}), grounded in **per-label indexable fields** (Point=content, Event=subject+name, Subject=name, Document=_searchText), ≥1 genuine no-match query per entity-type arm (intentional degrade trigger), ≥1 kind-bearing query per arm (structural contribution), structural as explicit strategy-class. Mix source stated (session/query logs if any exist; else documented synthetic mix + sensitivity analysis) with RNG seed.

### Task 2 — In-path trace wiring (search_engine.py + sdk.py) — review-gated hot-path diff
- `_elapsed` sink writes in `run_fts_query` / `run_vector_query` / `run_structural_query` on **EVERY return path** (breaker-open pre-try, structural no-conditions, index-miss benign, query_vec-None skip, operator FTS branch, HNSW-try AND brute-force-try, except paths). AC: no arm reports zero elapsed where samples were collected.
- `degradation_chain`: `per_strategy_elapsed`/`collection_stats` out-sink; **driver is the SOLE capping authority** — at `as_completed` TimeoutError the driver records `capped:set[str]` (membership = not-yet-yielded at deadline, pending ∪ completed-but-uncollected) + driver-observed elapsed (≈cap) keyed `(sample_id, strategy, "capped")`; runners write true elapsed to a SEPARATE `per_strategy_elapsed_true` key (uncensored column only). Shared mutable trace sink = **contextvar (default None)** — single mechanism; set → call → read after return (ThreadPoolExecutor copies context at submit; `shutdown(wait=True)` joins before read). Existing `degradation_chain` tests pass with sink=None (wiring AC).
- **Bench-only elevated timeout — 4 mechanisms, not 3:** (a) `as_completed` deadline, (b) DB-level `graph.query(timeout=...)`, (c) runner post-hoc `elapsed > timeout_ms` self-censor, (d) `shutdown(wait=True)` join. ONE bench config value `elevated_timeout_ms` (5000) threaded into: chain passthrough (forwards to EACH `executor.submit` + replaces hardcoded 0.5), isolation-arm direct calls, fallback `self.query`. AC: uncensored per-strategy elapsed > 500ms observed on slow-arm sample (proves bypass). Censored column runs the **pristine** path (no passthrough). Settle/join gap ≥ elevated timeout + margin between elevated-cap arms (zombie-thread protection). Bench-side watchdog (outer deadline > elevated timeout + join margin) classifies hang as new failure class.
- sdk.py `trace` out-param: **11 enumerated stage boundaries** with file:line anchors — encode, chain-submit, chain-join, fusion, kind-filter, relationship/traversal filters, exclude_status, EP annotation, entity fetch, relationships, serialization — + `wall_e2e`. Budget-reconciliation AC: `|Σ boundaries − wall_e2e| ≤ tolerance` per arm, else run invalid. Signature-pinned: `order_by=relevance` (prod default), pinned limit; graph/confidence paths out of scope.
- **Failure taxonomy** (4 classes, per-column cap as parameter): (1) elapsed≈cap + fail ⇒ CAPPED; (2) elapsed≪cap + fail ⇒ INVALIDATING (connection/DB-down); (3) success + elapsed≤cap ⇒ healthy; (4) success + elapsed>cap ⇒ CAPPED-TAIL (driver ignored timeout, #561 branch). Cap-share = class 1 + class 4.
- TF-IDF trigger for degraded mode: **embedding-less corpus** (vector returns [] via "No Points with embeddings" benign path) + no-match query + kind=None → all legs empty → fallback fires naturally in-path; `TORTOISE_BENCH_TFIDF=1` env hook (default-off, inert when unset, scoped to a task) forces `EmbeddingModel.get()→None` for run_report (non-pytest path). TF-IDF documented as degraded-mode-only; only sklearn branch measured (BERT-loaded branch out of scope, near-unreachable in healthy prod-parity corpus).

### Task 3 — Corpus providers (`benchmarks/synthetic_corpus.py`)
- **10K/100K arms:** seeded random 384-d vectors via batch UNWIND (HNSW traversal latency depends on vector distribution, not semantics) **PLUS EP-structure injection**: sampled fraction with `posterior_alpha/beta`, operator edges (IMPL/NAND/hasPart) at realistic density, pointKind spread, retracted fraction. AC: structural results NON-EMPTY in hybrid arm provenance (3-leg hybrid preserved at scale). If demo/replay corpus has real embeddings, seed synthetic vectors by sampling/perturbing them (preserve cluster structure); else document caveat. Synthetic text seeded from real corpus + skewed term distributions (FTS posting-list realism), skew params in provenance.
- **Index verification, not creation:** `Projection._ensure_indexes` (projection/__init__.py:861) auto-creates Point HNSW (384-d) + FTS indexes at boot (Docker-only). Per-arm provenance hard-requires the `db.idx.vector.queryNodes` path actually ran (probe index presence; FLAG brute-force fallback per search_engine.py:411-423). Synthetic index creation applies the captured real-corpus index params (M/efConstruction/efSearch) or the scaling run is non-comparable. `entity_type=point` pinned for scaling arms (only Point HNSW auto-created; non-Point structural kinds unindexed by design #522 — flagged for quality study).
- Idempotent named graphs; index-build duration recorded; wait-for-index-build-complete step before timing arms.
- **Current tier:** EventLog+fold replay conditional (projection.fold exists in-repo; real content + EP structure without touching live graph) / demo-seed fallback with point/edge counts in provenance. **Minimum-corpus floor** pre-registered (docs/entities/edges) below which the E2E-8 verdict is marked non-representative or withheld (fallback source = 10K synthetic, explicitly labeled).
- **Slow-arm mechanism (probed before full run):** (a) no-match query → post-chain TF-IDF full fit over 100K via `self.query(kind=None)` — E2E budget, uncensored; (b) index-drop brute-force vector arm over 100K (`vec.euclideanDistance` full scan, search_engine.py:411-423, labeled non-prod-parity) — chain cap + uncensored; (c) optional resource-contention arm (concurrent encode + queries). **Contingency ladder pre-registered:** scale to 1M → report AC as UNVERIFIED with observed value (record-don't-block) → accept BERT-encode path. Low-selectivity structural REJECTED (pointKind range-indexed + LIMIT-bounded → sub-10ms; cites 316's "<100K trivially fast").

### Task 4 — Bench pytest suite (`tests/bench/` + marker registration)
- `[tool.pytest.ini_options] markers=["bench"]` + `addopts = -m "not bench"` in pyproject (no markers config exists today).
- **All breaker hygiene in `bench_core.arm_runner()`** — single choke point used by BOTH pytest suite AND run_report.py: `reset_circuit_breakers()` (search_engine.py:102) between arms + pre/post breaker-state snapshots; breaker-open = **first-class failure-class in the capped column for ALL strategies**; short-circuited samples (OPEN, ~0ms) bucketed separately, never in p95; **verdict guard**: non-trivial breaker-open/cap share → flag "degraded-fast", report healthy-path AND degraded-path p95. Isolation arms: per-sample breaker reset (cold-breaker per-sample latency = the strategy's own latency).
- Tests: strategy isolation (direct `run_*_query` calls, elevated timeout), under-cap (degradation_chain pristine), budget composition (trace), cold/steady, scaling parametrization, trace-instrumentation no-op verification (trace=None byte-identical). **Logic-level tests DB-agnostic/mocked; measurement runs Docker-only marker-gated; guard asserts `TORTOISE_DB_URI` is not falkordblite embedded when bench marker runs.**
- CI (python-ci.yml): smoke = small seeded corpus only (skip 10K/100K), availability-gated skip (Docker FalkorDB ≥4.x AND `[embeddings]` extra), latency assertions warn-only + **fail-on-harness-error gate** + publish report artifact. (Note: ubuntu runners lack Docker FalkorDB → structural smoke; real runs are the local Task-6 artifact.)

### Task 5 — Report runner + runbook (`benchmarks/run_report.py`, `docs/benchmarks/2026-08-hybrid-latency.md`)
- **Two pre-registered numbers:** (1) **E2E-8 verdict** ← censored column (production truth at customer surface): mix-weighted p95 ≤ 300ms → achieved; verdict bands: ≤300 achieved / 300–500 **cap-dominated** (cap-share + dominant strategy) / >500+ε **join-tail beyond driver timeout** (investigate enforcement). Verdict column gets ≥100 samples or explicit p50/p95-only with p99 marked. (2) **#317 headroom** ← elevated-cap full-E2E column: headroom = 300 − full-E2E-uncensored-p95; retrieval-only p95 reported separately (for retrieval p95 + cross-encoder estimate). Columns may disagree (cap vs engine) — report maps which number drives which downstream decision (E2E-8 record-don't-block; #317 gate per headroom).
- **Auto-captured env + provenance block:** host CPU/RAM/arch (platform/os), container limits (docker inspect/compose), Docker + FalkorDB versions, **embedding model identity** (name/version/dimension), index params (M, efConstruction, efSearch, FTS tokenizer), corpus fingerprint (seed, doc count, retracted fraction, generated_at, git SHA), query_mix version, synthetic-vs-real topology stats (degree distribution), **image digest** (tested AND prod-deployed `:latest` with drift note), per-session provenance stamps (before/after). Bench-specific compose override: pins FalkorDB image tag + cpu/mem + index-build timeout.
- **E2E cold-start record:** first-N queries through the MCP client after fresh server+DB boot, same censored/uncensored split, per-sample/mean reporting for small N (percentiles meaningless at tiny N). 100K cold-start includes HNSW index load.
- **Per-strategy pass/flag columns** for FTS<50ms / vector<100ms / hybrid<200ms / TF-IDF<500ms — closes the original acceptance; dominant-strategy recommendation backed by target deltas.
- **MCP conformance:** ≥50 samples per mix-group (or bootstrapped CIs) via **stdlib urllib/http.client** against the server's Streamable HTTP transport (NO new dep — claim verifiable); transport brought up FIRST (mcp_auth `_safe` fails closed when `_transport_mode` is None → auth-error dicts ~0ms would falsely satisfy p95≤300ms); AC every conformance sample non-error (auth errors → invalidating); pinned tool args (limit/threshold/kind per mix group); semantics aligned with `_emit_mcp_tool_call_telemetry` latency_ms (tool-execution-only) — transport-in-budget stated explicitly. SDK arms carry the 11 boundaries in-process; MCP arms measure client-side wall-clock (transport-bound purpose).
- **Measurement order pre-registered:** censored → uncensored → elevated, warm-up re-run after each config switch; transport bring-up in Task 6 preconditions.
- Runbook links research brief (docs/research/2026-08-13-316-vector-benchmark.md) + arXiv 2409.06464. Updates stale "#7700 benchmark" refs in docs/plan/2026-08-03-hybrid-search-plan.md (L445/L477). Docs filed per docs/00_index.md.

### Task 6 — Full benchmark run + verdict
Report committed with: E2E-8 verdict (per corpus size: production-scoped verdict + per-size headroom context), dominant strategy + optimization recommendation, #317 handoff section (per-mix p95 headroom, per-strategy candidate-pool latency, scaling ceiling, reusable `pytest tests/bench -m bench` baseline for before/after regression).

### Testing Strategy (validating the harness)
- Determinism: pure-helper unit tests (known arrays → known percentiles; right-censor math; seeded corpus → identical structure).
- No-op regression: trace=None byte-identical; full existing suite green (diff is default-off).
- Availability skip: Docker probe failure → `pytest.skip` (mirrors test_hnsw_vector_index.py).
- Soft vs hard: structural failures hard-fail; latency thresholds warn-only (generous margins); harness errors fail the CI gate.

### Acceptance Criteria
- **AC1** Trace wiring default-off; full existing test suite green; `degradation_chain` tests pass with sink=None.
- **AC2** `pytest tests/bench -m bench` measures all 4 strategies in isolation AND under the cap, with capped + elevated-cap (uncensored) percentiles; auto-skips without Docker FalkorDB ≥4.x or `[embeddings]`; falkordblite guard.
- **AC3** Budget composition covers all 11 stages; `|Σ − wall_e2e| ≤ tolerance` per arm; wall vs Σ documented.
- **AC4** Cold-start (per-strategy + E2E first-N) measured separately from steady-state; warm-up recorded (incl. non-convergence flags).
- **AC5** Scaling arms 10K/100K EP-structured, reproducible, HNSW-verified (queryNodes path exercised, brute-force flagged); provenance explicit per arm.
- **AC6** Report contains: env/container specs, corpus provenance + topology stats, pinned mix, isolation + under-cap results, budget composition, cold/steady, scaling table, per-strategy target flags, image digests.
- **AC7** 300ms verdict per corpus size with the operationalized mix definition + verdict band; if unachievable: dominant strategy + optimization (matches #316 acceptance).
- **AC8** #317 handoff section (headroom = 300 − full-E2E-uncensored-p95; retrieval-only p95; scaling ceiling); #317 can re-run `-m bench` before/after.
- **AC9** Docs filed; plan-doc stale #7700 refs updated; raw JSONL in `data/bench/`.

### Runtime Prerequisites
Docker FalkorDB ≥4.x (HNSW+FTS; embedded FalkorDBLite NOT prod-parity — numbers can reverse), `[embeddings]` extra (sentence-transformers baked in Docker image, HF_HUB_OFFLINE=1), host specs recorded (harness auto-captures), **no new dependencies**, runbook at docs/benchmarks/2026-08-hybrid-latency.md.

### Decision-Gate Handoff to #317
#317 gates on: (1) per-mix p95 headroom (300 − full-E2E-uncensored-p95) bounding the reranker budget (CPU cross-encoder ~210-410ms/10-100 docs per #317 research → candidate-pool size); (2) per-strategy isolation p95 for the candidate pool each strategy feeds; (3) corpus-size scaling ceiling where budget is exhausted; (4) reusable `pytest tests/bench -m bench` baseline. **This issue supplies the evidence; #317 consumes it.**

---

## Clarifications

*(from issue-pre round — 2026-08-13)*

| Question | Answer | How |
|---|---|---|
| Competitive baselines: deploy Neo4j/Supermemory/Honcho as live systems vs documented reference? | Documented reference (D8 patterns + external single-source numbers with caveats); NO competitor deployments in this issue — SaaS plumbing is a separate project | resolved by research + recommendation applied (flagged open for human) |
| Labeled test set: hand-labeled vs synthetic vs BEIR-style? | P/R@K deferred to quality study (no set exists, relevance for epistemic memory subjective, cross-vendor incomparable); this issue measures latency only | resolved by research + recommendation applied (flagged open for human) |
| Who owns the labeled retrieval set for the deferred study? | epistemic-team (issue team field); the tracking issue records the gating condition (real query logs + labeled set) | recommendation applied (flagged open for human) |
| Does the 300ms budget include embedding compute? | YES — sdk.py computes the query embedding in-path before the degradation chain | resolved by code |
| Who consumes results? | #317 gating (latency headroom) + E2E-8 latency assertion formatting | resolved by issue graph |
| Deployment target? | Docker/HNSW (prod parity); embedded FalkorDBLite explicitly excluded (brute-force, numbers can reverse) | resolved by code + research |

### Deferred to Research
*(researchable questions answered in Phase 1.5)*

- Precision/recall@K target conventions → answered (IR eval: ~50 queries min; p@k/r@k/nDCG; quasi-gold leaks) — feeds deferred quality study
- Is latency the right primary metric vs retrieval quality? → answered (latency budget is the #317 gate; quality deferred; F1 answered by #317's internal criterion)
- Should EP-annotated ranking quality be evaluated? → deferred to quality study (no labeled set)
- Tiny-graph validity of vector benchmarks → answered (HNSW-vs-flat negligible <100K; FLAT better <5K; min-corpus floor pre-registered)
- Cold/warm measurement protocol → answered (percentile protocol; cold = fresh process/first-N)
- RRF k sensitivity → answered (k=60 standard; 20-100 similar; optional sensitivity arm as P4 stretch)
- CI vs manual runbook → answered (manual full run + CI smoke, marker-gated, availability-skipped)
- Hardware parity → answered (host specs auto-captured; bench compose override pins cpu/mem; 2GB VM prod note)

---

## External Research (Phase 1.5 artifact)

Persisted: docs/research/2026-08-13-316-vector-benchmark.md (6 source-tagged entries). Budget: Fast intent, Standard ≤8 post-dedup — 8 used. Dedup: D8 research (docs/research/2026-07-18-conversation-indexing-search.md) covered Honcho/Graphiti/Mem0 competitive patterns.

### Axis Research

**Architecture (high):**
- FalkorDB vector index: sub-linear query time to millions of vectors; official benchmark tool with p50/p90/p99 methodology + Neo4j comparison [canonical — FalkorDB docs, github.com/FalkorDB/benchmark, benchmark.falkordb.com]
- HNSW vs brute-force: negligible <100K docs; FLAT preferred <5K vectors / <10MB index; gap grows 100K-1M (flat 2-3× slower) [pitfalls — arXiv 2409.06464, SurrealDB docs] → drives the 10K/100K scaling arms + min-corpus floor; agent-memory scale trivially meets vector<100ms
- Latency protocol: p50/p95/p99 with warm-up, ≥100 samples for stable p99, p95 for regression gates, record full-chain completion [canonical — DigitalOcean LLM-inference guide, gatling.io, loadtester.org]
- IR evaluation: ~50 information needs minimum (Manning IR book); 50-200 golden pairs (RAG practice); tuning on the same collection overstates performance — quasi-gold self-retrieval leaks [canonical — Manning, Stanford IR book, dataaihub] → feeds deferred quality study
- RRF: k=60 industry standard (Cormack 2009; Elasticsearch + Azure AI Search default 60); k 20-100 behaves similarly; higher k for keyword+vector fusion; tune only with labeled data [canonical — Elasticsearch, Azure AI Search, MariaDB] → validates codebase k=60
- Competitor reference: Neo4j hybrid top-10 on 10M graph p50 ~250ms / p95 ~340ms (⚠️ single-source markaicode); Neo4j vector 23.7ms@k=10 → 44.3ms@k=100 vs FAISS-HNSW 4.5-4.7ms (KTH thesis); Supermemory sub-300ms / 85.4% LongMemEval (⚠️ vendor marketing, single-source) [competitor-precedent] — reference-only for the "correctness" verdict; deferred study owns cross-vendor comparison

**Library-deps (triggered):**
- No new dependencies: pytest≥8, numpy, scikit-learn (TF-IDF), falkordb, falkordblite all in-repo; sentence-transformers via `[embeddings]` extra (baked in Dockerfile.hosted); timeit/pytest suffice for percentiles; BEIR/ir-measures NOT needed (P/R@K deferred) [canonical — in-repo verification]

### Integration Docs

| Dep | Version | API-surface findings |
|---|---|---|
| falkordb | 1.6.2 (existing core dep) | Driver-level `graph.query(timeout=...)` passthrough — verified in use in search_engine.py; the 500ms kill is server-side |
| falkordblite | 0.10.0 (existing) | **NOT used for benchmark** — embedded = brute-force vector, no FTS/HNSW; explicitly excluded (numbers can reverse) |
| sentence-transformers | >=3,<6 via `[embeddings]` extra | all-MiniLM-L6-v2 384-dim; baked in Docker image; HF_HUB_OFFLINE=1; in-path encode is inside the 300ms budget |
| scikit-learn | >=1.0 (existing dev dep) | TF-IDF fallback engine (search_points degraded mode) |
| Docker FalkorDB | >=4.x (prod deployment) | HNSW + FTS auto-created by `_ensure_indexes` (projection/__init__.py:861); **Point-only HNSW** (384-d); version-gated |
| MCP conformance client | none (stdlib urllib/http.client) | Streamable HTTP transport; `_safe` fails closed when `_transport_mode` is None (mcp_auth.py:12-13) |

---

## Rejected Alternatives

| Approach | Why not chosen | When it WOULD have been better |
|---|---|---|
| **A — Black-box SDK driver** (standalone script, monkeypatch wrappers, no prod diff) | Budget composition is wrapper-inferred (brittle to refactors); CANNOT see the internal elapsed timers (right-censoring requirement unmeetable); live-graph non-reproducible; one-shot artifact, no #317 regression surface | Hot-path change freeze or review gate unaffordable; explicitly one-shot report with zero ongoing maintenance; search_engine.py/sdk.py slated for rewrite |
| **C — Process-isolated replay driver** (orchestrator + worker subprocesses, fresh worker per scenario) | Heaviest orchestration for Standard tier; every scenario pays model-load cold cost (conflates the cold/steady split the problem explicitly separates); replay fidelity depends on unverified event-log content; does NOT solve right-censoring without B's diff anyway | Airtight cold-start isolation is the PRIMARY measurement; live graph unavailable or known-corrupting; singleton/breaker state contamination is a demonstrated problem |
| **Pure SPLIT** (Devil's-advocate proposal: narrow latency-only issue + separate deferred quality study) | Correct execution SHAPE, adopted within the framing — but splitting the issue would orphan E2E-8's formatted assertion and the 4-strategy contract; the confirmed problem already defers P/R@K + competitors while keeping the latency deliverable whole | If #316's acceptance were latency-only and E2E-8 owned elsewhere |
| **One-shot timeit script** | Cannot separate censored (cap-killed) from uncensored completion; cannot isolate cold vs steady; cannot be re-run for #317 regression; no provenance | The decision were a single question answerable on day one (the plan's own §6 "timeit sufficient" note — superseded by the #317 gating need for trustworthy headroom) |

---

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| FalkorDB (Docker ≥4.x, HNSW+FTS indexes) | Data store | Tasks 2/3/4 (sink wiring, verify-not-create, Docker gate, falkordblite guard) | ✅ |
| FalkorDB driver timeout semantics (500ms kill) | Data store | Task 2 (driver capping authority, 4-mechanism elevated timeout) | ✅ |
| Embeddings (in-path encode, 384-d model) | Library | Task 2 (11 boundaries incl. encode) + `[embeddings]` extra requirement | ✅ |
| Search engine (search_engine.py + sdk.py) | Core code | Task 2 trace wiring (review-gated, default-off) | ✅ |
| Circuit breakers (module-level 3-fail/30s) | Core code | Task 4 arm_runner (resets, snapshots, breaker-open class, degraded-fast guard) | ✅ |
| Test data (synthetic 10K/100K EP-structured + query mix) | Data | Tasks 1/3 (seeded, idempotent, HNSW-verified) | ✅ |
| Current-tier corpus (EventLog replay / demo seed) | Data | Task 3 (conditional provider, min-corpus floor) | ✅ |
| Report output (docs artifact + data/bench JSONL) | Deliverable | Tasks 5/6 (run_report.py, verdict bands, provenance block) | ✅ |
| MCP customer surface (mcp_server.py tortoise_search) | API | Task 5 (conformance ≥50, transport bring-up, auth-error invalidation) | ✅ |
| #317 cross-encoder gating (decision gate handoff) | Consumer | Task 6 + headroom formula (300 − full-E2E-uncensored-p95) | ✅ |
| CI (python-ci.yml smoke) | Cross-cutting | Task 4 (marker-gated, availability-skipped, fail-on-harness-error) | ✅ |
| docker-compose (bench override: image tag + cpu/mem pins) | Infra | Task 5 | ✅ |
| pyproject (bench marker registration) | Config | Task 4 (`markers=["bench"]` + addopts) | ✅ |
| Docs (00_index.md filing, stale #7700 refs) | Docs | Task 5 | ✅ |
| Deferred quality study (P/R@K + competitors) | Soft dep | Tracking issue filed (linked #316/#317) | ✅ |

**<HARD-GATE> Wiring check: PASS — all touch points covered. No gaps.**

---

## Review Cycle Log

| Gate | Cycles | Outcome |
|---|---|---|
| problem-verify | 1 | PASS (0 P0/P1; P2+ incorporated) |
| solution-verify | 5 | PASS (each cycle fixed the prior P1s; cycle 5: 0 P0/P1) |
| coherence check (Qwen-substitute) | 2 | Converged (P1s fixed each cycle; provider-blocked Qwen surfaced as [QWEN-GATE]) |
| Wiring check | 1 | PASS (all 15 touch points covered) |

Per Phase 7: the two verify gates + coherence check (all fresh-context `task` sub-agents) serve as the parallel review cycle — 11 fresh-context review dispatches total (2 diverge + 2 converge + 2+2 verify + 5+5 solution-verify + 2 coherence + 1 codebase explorer + 1 diverge + 1 converge + 1 Qwen-substitute cycles across the run). No additional parallel review agents dispatched — gates covered the four skill roles (codebase/docs review = Codebase Explorer + verifiers' code verification; UX = N/A (UX_RATING low, no UI); epic alignment = #317/#7697 linkage verified in falsification check; Devil's advocate = Agent B in Phase 1 + coherence reviewer).

---

## Complexity

| Domain | Rating | Rationale |
|---|---|---|
| **UX** | low | SDK/CLI/report only — no UI changes; MCP surface measured, not modified |
| **Ontology** | low | No new vocabulary; benchmark adds a report + test files; no graph schema changes |
| **Architecture** | medium | New benchmark subsystem (harness + trace instrumentation + report) — but measurement-only, default-off, no runtime behavior change; ~30-line hot-path diff review-gated |
| **Accessibility** | low | No user-facing UI |
| **Library-deps** | low | Zero new dependencies (verified: pytest/numpy/sklearn/falkordb in-repo; stdlib MCP client) |
| **Test** | medium | New bench suite + trace no-op verification + deterministic harness tests; benchmark-as-test pattern |
| **Risk** | medium | Hot-path instrumentation (mitigated: default-off, review-gated); measurement validity (mitigated: 5 verify cycles, probe-before-run, verdict guards); CI flakiness (mitigated: warn-only + availability-skip) |

**Tier: standard** (matches issue Complexity + expected tier; no skill-domain upgrade trigger — no skills/ or shared-code paths touched).
