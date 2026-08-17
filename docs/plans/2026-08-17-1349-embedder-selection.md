<!-- research-path: docs/research/2026-08-17-1349-embedder-selection.md -->

# #1349 Embedder Selection — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Select the best 384-dim server-side embedding model for hosted Tortoise via pre-registered evidence (LongMemEval-S co-primary turn_recall@10 + nDCG@10 + mini-BEIR + hard tier + E2E-8), then conditionally land the swap (embeddings.py model, Docker bake, backfill re-embed, threshold recalibration).

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** Two-PR, evidence-gated. PR1 (unconditional, zero production-code changes): benchmark tooling + all evidence on main — probe injection seam, LongMemEval vector-only arm, bootstrap one-sided-p + BH-FDR + gate script, labeled-pair fixture, mini-BEIR harness, UX research + ADR-009 (Pending). Gate between PRs: `gate_1349.py` verdict + provenance audit + user product call (BEFORE the burn) + HNSW spot-check + pre-swap E2E-8 — all HARD PR2 preconditions. PR2 (conditional): `EMBEDDING_MODEL` constant + swap, Dockerfile re-bake + entrypoint FATAL, CI cache v2, backfill `--force-re-embed` (6 labels), no-DROP same-dim index rebuild, threshold recalibration, E2E-8 confirmation, ADR-009 → Accepted.

**Issue:** #1349 · **Level:** task · **Complexity:** standard (reconciled in scoping — HIGH library-deps axis conditional-isolated: PR1 zero prod deps; route-integrity note: escalate if plan-verify/implementation reveals project-scale needs)

---

### Pattern Research

> **Findings date:** 2026-08-17

> **Gate skipped: plan introduces ZERO new third-party dependencies.** sentence-transformers is an existing in-repo extra (`pyproject.toml:37`, `>=3,<6` pin, used 2+ places: embeddings.py, tests); the 4 model candidates (snowflake-arctic-embed-xs/s, bge-small-en-v1.5) were externally verified in Phase 1.5 with MTEB-R figures + HF sources (docs/research/2026-08-17-1349-embedder-selection.md); mini-BEIR + LongMemEval use stdlib urllib only (no `datasets`/`beir`/`parquet` deps — pinned in scoping T6).

**Library docs (preflight)** — sentence-transformers (existing, version-pinned in-repo): no lookup needed. All 4 candidates verified sentence-transformers-loadable (no `trust_remote_code` for the 384-dim pool).

**Library version & API surface** — skipped: no new dep versions introduced.
**Idiomatic usage patterns** — skipped: follows the existing in-repo pattern (`EmbeddingModel` singleton + `SentenceTransformer(...)` at embeddings.py:108; monkeypatch seam precedented at tests/test_embeddings.py:258-269).
**Library/framework pitfalls** — skipped: dep used identically elsewhere in-repo with documented handling (degrade chain, `HF_HUB_OFFLINE`, cold-start #545).

---

### Integration Surface Map

Derived from the scoping wiring table (code-verified across 5+ verifier passes). Test layers per surface:

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | `tortoise/embeddings.py` (:108 model literal, :27-28/:210/:253 thresholds) | State | Both | Unit | `EMBEDDING_MODEL` constant; 384-dim assert; thresholds model-specific | Load failure degrades to TF-IDF (must HARD-FAIL under probe); threshold drift |
| 2 | `tortoise/cross_lens.py` (:31-32 duplicate thresholds) | State | Read | Unit | 0.40/0.75 in sync with embeddings.py | Divergent constants post-recalibration |
| 3 | `tortoise/sdk.py` cosine sites (:2858-2859 dedup 0.60/0.92, :5812/:5862/:5961 0.40, :7692 checkpoint 0.95) | DB | Read | Unit + Integration | Cosine bands vs dedup/review/checkpoint semantics | Threshold shift silently changes dedup aggressiveness (invisible to nDCG) |
| 4 | `tortoise/search_engine.py` (:340 384-dim contract, run_vector_query) | DB | Read | Integration | query_vec 384-dim; HNSW vs brute-force | Dim mismatch; circuit breaker; mixed-space vectors |
| 5 | `tortoise/projection/__init__.py` (:1365/1381 single Point.embedding 384-dim index) | DB | Write | Integration (Docker-gated) | CREATE VECTOR INDEX; auto-update on SET | Index drop needed only for dim change (not this pool) |
| 6 | 6-label embedding surface (entities.py:171/284/331/405/528 + backfill Source.url) | DB | Write | Integration | Point/Subject/Object/Document/Event/Source | Event/AgentSession text composition (session_embedding_text); mixed old/new vectors during batched re-embed |
| 7 | `graph-scripts/backfill_embeddings.py` | State | Write | Unit + Integration | `--force-re-embed` flips NULL-only WHERE; idempotent; direct-Cypher (no PointRevised clobber) | Partial re-run; dedup/review-connections degrade during window |
| 8 | `Dockerfile.hosted` / `entrypoint.sh` / `hosted_api.py` | External (deploy) | Out | Deploy-workflow | Bake cache path org-qualified; FATAL-if-missing; pre-warm model-agnostic; reject TORTOISE_EMBEDDER_OVERRIDE in prod | Present-but-corrupt cache passes FATAL → silent degrade (add /health signal); selfhost lazy first-use download |
| 9 | `.github/workflows/python-ci.yml` | External (CI) | Out | CI | Cache key v1→v2; new test files registered in halves/SLOW_FILES | Unregistered tests never run; HF_HUB_OFFLINE breaks candidate-model tests (skip-if-not-cached) |
| 10 | `tools/longmem_eval/` (retrieve.py, run.py, dataset.py) | State | Both | Unit + Integration | vector_search() never calls tortoise_fts_query; `--retriever/--model/--retrieval-only`; per-model checkpoint keying `{retriever}__{model}__{prompt}` | Encode-degrade → embedding-less graph → empty recall (abort MODEL_ENCODE_FAILED); checkpoint cross-model collision |
| 11 | `tests/eval/retrieval/` (run.py, bootstrap.py, metrics.py) | State | Both | Unit + Integration | one-sided bootstrap p + BH-FDR m=6; gate_1349.py; nDCG@10 binary-gain definition | Bar arithmetic (m=6 z≈2.128); report denominator mismatch; --limit subset mixing |
| 12 | `benchmarks/run_report.py` (:639 provenance literal) + `synthetic_corpus.py` (:29) | State | Read | Unit + Integration | provenance reads EMBEDDING_MODEL constant; E2E-8 ≤300ms p95 | Post-swap reports lie about embedder; E2E-8 measured post-sunk (must be pre-swap pre-condition) |
| 13 | `tests/test_embeddings.py:269` | State | Read | Unit | asserts the constant, not the literal | Breaks on rename |
| 14 | Docs (ADR-009, research dir, 00_index, embedding-retrieval.md) | External | Out | — | ADR-009 Pending→Accepted; per-label blast radius; tenant-visible changes | Decision record lost on negative gate (must ship in PR1) |
| 15 | #265 encrypted tier (pending-merge, NOT in code) | Future | — | — | 384-dim = chosen scope cut + escape clause | #265 lands non-384 before PR2 → pool reopens |
| 16 | Self-hosted (Dockerfile.selfhost) | External (deploy) | Out | — | No bake; lazy first-use download; TF-IDF degrade documented | Blocked-network operators get silent degrade (pin-old-image escape) |

### Bug Pattern Flags
- **Silent function skips:** `EmbeddingModel.get()` degrade → TF-IDF must never silently stand in for a requested `--model` (HARD FAIL + MODEL_ENCODE_FAILED marker). Historical: #399/#880 degrade-path incidents.
- **Conditional guards:** entrypoint FATAL checks cache-dir PRESENCE only — corrupt-cache passes (add model-identity/degraded signal). Historical: #160 booted-with-missing-cache.
- **Race conditions:** encode-cache must be model-keyed (`sha256(model_id + prompt_name + text)`) — content-hash-only lets MiniLM vectors serve arctic runs. Per-model checkpoint keying prevents cross-model resume collision.
- **N+1/queries:** cross-question encode caching (overlapping haystack content re-ingested per question — 5-10× redundant encodes).

### Checklist Notes
- Empty-vs-null: zero-evidence-turn questions → nDCG@10 = 0.0 (included in mean), matching turn_recall@10's report default.
- Boundary values: gate_1349.py unit tests at n=200/300/500 with m=6 expected bars (+15.1%/+12.3%/+9.5%); degenerate control-mean (<0.05 → absolute ≥0.30); multi-winner.
- Failure modes per surface tested: encode-degrade (search-time assert), HF_HUB_OFFLINE (skip-if-not-cached), Docker availability (5-day escalation), download failures (selfhost).

### Verification Plan (test-routing: code domain, standard tier)

- **Unit:** probe injection (test_embedder_probe.py), bootstrap p + BH-FDR + gate branches (test_gate_1349.py), nDCG@10 binary-gain (test_vector_arm.py), backfill --force WHERE predicate (test_backfill_embeddings_force.py), labeled-pair schema (test_labeled_pairs_schema.py), mini-BEIR smoke on 10 queries (test_mini_beir.py).
- **Integration (embedded FalkorDBLite, no Docker):** vector arm on mini fixture (`--mock` parity, zero tortoise_fts_query calls, per-model checkpoint isolation), eval run.py `--model` param, bootstrap paired machinery.
- **Integration (Docker-gated, `@slow`):** real-model baseline, HNSW spot-check, E2E-8 latency, post-swap recall spot-check — NOT in CI (benchmark machine per T8).
- **CI:** new test files registered in **both `config/ci-surfaces.yml` (fail-closed `ci_selection.py --integrity` gate) AND python-ci.yml halves/slow list**; candidate-model tests skip-if-not-cached (HF_HUB_OFFLINE) with a **zero-skip sentinel (broken cache fails CI loudly)**; mini-BEIR tests fixture-only (never live downloads); **committed artifacts scanned for key-like patterns (no OPENROUTER_API_KEY leak)**.
- **Deferred non-code domains:** UX research deliverable (research domain → its own verification via content/ux checks in plan-review), ADR decision record (docs).

---

## Task List

### Task 1: Probe injection seam (`tools/embedder_probe.py`)

**Intent:** Enable the harness to run any of the 4 candidates without touching production code — the mechanism that makes the gate runnable and survives as the future rotation tool.
**Acceptance:** `inject_model(name, query_prompt=None)` monkeypatches the `SentenceTransformer` symbol in `sentence_transformers` BEFORE the first `EmbeddingModel.get()`, calls `_reset()`, asserts 384-dim + non-None (HARD FAIL, no silent TF-IDF degrade), records model_id; `reset()` restores; `PROBE_MODELS` registry maps minilm/arctic-xs/arctic-s/bge-small → HF ids. **Warm-process injection (plan-review P1 fix):** inject_model must genuinely swap even if `EmbeddingModel.get()` was already called in-process (verified via a discriminating-embedding check — cosine distance to a reference vector must differ) or HARD-FAIL; no pre-injection cache entries served under the new model key. **CI registration (plan-review P1 fix):** register `test_embedder_probe.py`, `test_vector_arm.py`, `test_gate_1349.py`, `test_mini_beir.py`, `test_backfill_embeddings_force.py`, `test_labeled_pairs_schema.py`, `test_calibrate_thresholds.py`, `test_pair_label_runner.py` in **BOTH `config/ci-surfaces.yml` (fail-closed `ci_selection.py --integrity` gate — unlisted top-level tests block every PR) AND the python-ci.yml halves/SLOW_FILES lists**; candidate-model tests skip-if-not-cached (HF_HUB_OFFLINE) with a CI sentinel that FAILS the job on zero-skip (a broken cache must not silently green).
**Files:**
- Create: `tools/embedder_probe.py`
- Modify: `config/ci-surfaces.yml`, `.github/workflows/python-ci.yml`
- Test: `tests/test_embedder_probe.py` (incl. warm-process + discriminating-embedding cases)

### Task 2: LongMemEval vector-only arm

**Intent:** The primary gate metric surface — turn_recall@10 + nDCG@10 over the vector arm only (per locked rule).
**Acceptance:** `vector_search()` in retrieve.py encodes query via injected model, calls `run_vector_query(proj.g, qvec, limit, is_embedded=getattr(proj,'_is_embedded',True), vector_index_api=getattr(proj,'_vector_index_api',None))`, NEVER `tortoise_fts_query`; asserts `n(embedding IS NOT NULL) > 0` before search (MODEL_ENCODE_FAILED on empty graph); **passes an ELEVATED timeout (5000ms, per the in-repo hard-tier precedent) and surfaces breaker-open state — a circuit-breaker trip must fail the question with a `breaker_open` marker, never silently count as recall 0** (plan-review P2 fix); emits per-question ranked ids + evidence-turn matches + nDCG@10 (binary gains, log₂(i+2), IDCG all-evidence-first capped 10, zero-evidence → 0.0) + **P@10 (secondary) + P@5 (tertiary) for #317 comparability** (plan-review P2 fix); run.py gains `--retriever {hybrid,vector}` (default hybrid), `--model`, `--query-prompt`, `--retrieval-only` (**report shape: accuracy sections omitted or null with methodology note — no bogus accuracy from unset labels**, plan-review P2 fix); checkpoint key `{retriever}__{model}__{prompt}` (versioned format); **checkpoint writes atomic (temp-file-then-rename); resume against truncated/corrupt checkpoint re-encodes just that question with a warning — never crash, never silently drop from the denominator** (plan-review P1 fix). **Encode cache (plan-review P1 fix):** build the model-keyed encode cache (`sha256(model_id + prompt_name + text)` scoped per config run) — this is what makes the 12-45h burn feasible (5-10× redundant encodes otherwise) and prevents MiniLM vectors serving arctic runs; unit test asserts the key includes model_id.
**Files:**
- Modify: `tools/longmem_eval/retrieve.py`, `tools/longmem_eval/run.py`
- Test: `tests/longmem_eval/test_vector_arm.py` (incl. empty-graph abort, breaker-open marker, elevated-timeout, encode-cache model-keying, checkpoint atomicity/truncation-resume, retrieval-only report shape)

### Task 3: Eval + benchmark parameterization

**Intent:** The in-repo hard tier and E2E-8 benchmark become 4-model surfaces — reproducible secondary evidence + the pre-swap latency precondition.
**Acceptance:** `tests/eval/retrieval/run.py` gains `--model`/`--query-prompt` (provenance records embedding_model); `benchmarks/run_report.py` gains `--model` override and reads provenance from the actual singleton/constant (fixes :639 literal); synthetic_corpus.py:29 comment consistent.
**Files:**
- Modify: `tests/eval/retrieval/run.py`, `tests/eval/retrieval/provenance.json`, `benchmarks/run_report.py`, `benchmarks/synthetic_corpus.py`
- Test: `tests/eval/retrieval/test_integration.py` additions

### Task 4: Bootstrap one-sided p + BH-FDR + gate script

**Intent:** The locked decision rule, as committed, CI-tested code — not a judgment call at merge time.
**Acceptance:** In `tests/eval/retrieval/bootstrap.py`: `one_sided_bootstrap_p(deltas, n_resamples, rng)`; `bh_fdr(pvals, q=0.10)`; `gate_1349.py` (new) implements the full rule: category filter (single-session-* OR {temporal-reasoning, knowledge-update, multi-session}, exclude _abs), paired deltas on question_id, co-primary turn_recall@10 + nDCG@10, win = (a) ≥+5% relative AND (b) BH q=0.10 over m=6 (z≈2.128, bars +9.5%/+12.3%/+15.1% at n=500/300/200), outcomes PASS/NO-WINNER/INSUFFICIENT-POWER + absolute-fallback (control<0.05 → ≥0.30) + multi-winner (argmax combined rank → E2E-8 → size → family-preserving) + escalation branch (neither metric clears + directional signal → end-to-end judged top-2). **P@10 (secondary) + P@5 (tertiary) reported in gate output** (plan-review P2 fix). **Gate input manifest (plan-review P1 fix):** gate_1349.py accepts a manifest of `{split, retriever, model, prompt, n, checkpoint_state, report_sha, code_sha}` per config; HARD-FAILS on (a) missing config (m must stay exactly 6 — no deriving FDR over 5/6), (b) report_sha mismatch vs on-disk (catches mid-burn reads / tampered evidence), (c) denominator mismatch (mixed-n `--limit` subsets, hybrid reports fed as vector, asymmetric question sets — reports dropped-question counts per pair and fails above a threshold), (d) code_sha drift (verdict committed at T8 not re-validated against drifted main). **CHECKPOINT: unit tests on synthetic data with known verdicts (incl. manifest-failure cases) reviewed BEFORE T8.**
**Files:**
- Modify: `tests/eval/retrieval/bootstrap.py`
- Create: `tests/eval/retrieval/gate_1349.py`
- Test: `tests/eval/retrieval/test_bootstrap.py` additions, `tests/eval/retrieval/test_gate_1349.py` (incl. manifest validation: missing-config, report_sha mismatch, denominator mismatch, code_sha drift, asymmetric question sets, dropped-question thresholds)

### Task 5: Labeled-pair fixture + calibration tool

**Intent:** The durable recalibration artifact — thresholds are model-specific, so the swap needs re-runnable calibration, not hand-tuning.
**Acceptance:** `tests/fixtures/labeled_pairs.jsonl` (≤200 pairs, **bands enumerated: near-dup 0.75-0.95, dedup review/auto-merge 0.60/0.92, paraphrase 0.35-0.51, noise <0.15 — ≥30 pairs per band**, seeded with the 5 #399 anchor pairs, model-agnostic text only); two LLM judges via OpenRouterModel + kappa math (KAPPA_GREEN=0.60) via a net-new thin pair-label adapter (~50-100 lines — kappa CLIs cannot consume pair semantics directly); `tools/calibrate_thresholds.py --model <c>` emits suggested bands (**empty-pairs and <min-samples-bands → explicit error, never NaN**; plan-review P2 fix); **pair_label_runner unit tests: kappa math against known agreement matrices, single-judge API failure → abort (never single-judge labels — kappa over one judge is vacuously 1.0), kappa < / ≥ 0.60 decision path + adjudication branch** (plan-review P2 fix); MiniLM run reproduces #399 bands (sanity).
**Files:**
- Create: `tests/fixtures/labeled_pairs.jsonl`, `tools/calibrate_thresholds.py`, `tools/pair_label_runner.py`
- Test: `tests/test_labeled_pairs_schema.py`, `tests/test_calibrate_thresholds.py` (incl. empty/degenerate inputs), `tests/test_pair_label_runner.py`

### Task 6: mini-BEIR research harness

**Intent:** Independent retrieval-quality signal across four BEIR datasets — research surface only (NOT a gate), feeds tiebreak + monitoring baseline.
**Acceptance:** `tools/mini_beir/run.py` + README: MS MARCO dev qrels-constrained top-1000 queries (100k-passage sampled corpus, documented not-leaderboard-comparable), NFCorpus/SciFact/FiQA full; nDCG@10 + R@10; BEIR raw tsv.gz/jsonl via stdlib urllib (NO parquet — zero new deps); `--model`/`--query-prompt`; MS MARCO treated as in-domain sanity (contamination note: arctic/bge trained on it — OOD datasets weighted); **dataset download path tested against corrupt/truncated cache files → re-download or clear error, never silent partial corpus; results JSONs digest-pinned (dataset sha256/size/date); empty and 1-passage corpora → defined output (R@10=0, nDCG=0), no crash** (plan-review P2 fixes); results JSON per model committed.
**Files:**
- Create: `tools/mini_beir/run.py`, `tools/mini_beir/README.md`
- Test: `tests/mini_beir/test_mini_beir.py` (smoke, fixture-only, incl. empty/corrupt-input cases)

### Task 7: UX research + ADR-009 + docs registration

**Intent:** Deliverables that must survive regardless of gate outcome — the hosted-vs-local answer + decision record.
**Acceptance:** `docs/research/2026-08-17-1349-embedder-selection/ux-research.md`: hosted-vs-local (mem0/Zep/LangMem server-side; local = self-hosted; ADR-009 explicit "local embedding offered to hosted tenants: NO" + "tenant-visible changes at launch" checklist + per-label blast radius + deploy checklist incl. rollback); self-host lazy first-use download + failure behavior + pin-old-image escape; `docs/adr/ADR-009-embedder-selection.md` (Status: Pending evidence, Date, Issue, Owner: full rule pre-registration + candidate pool + encode policy + outcome branches); register in `docs/00_index.md`.
**Files:**
- Create: `docs/research/2026-08-17-1349-embedder-selection/ux-research.md`, `docs/adr/ADR-009-embedder-selection.md`
- Modify: `docs/00_index.md`

### Task 8: Evidence production (stage-0 pilot → full burn → verdict)

**Intent:** Produce the complete, reproducible evidence set on main and supersede the synthetic baseline.
**Acceptance:** (1) **User product call asked NOW (T7-drafted) — written to a machine-readable decision file (`docs/research/2026-08-17-1349-embedder-selection/product-call.json`: enum server-side/selfhost-only/reject-swap + timestamp + recorder); no response in 24h → proceed with server-side default (recorded); gate preflight validates the file exists with a legal value** (plan-review P1 fix). (2) Stage-0 pilot n≈150 (MiniLM + arctic-s): control level, empirical paired-delta sd, rough delta → go/no-go (directional: control ≥ ~0.55 ceiling OR < +2pp → escalate nDCG/end-to-end or close with pilot evidence). (3) Full burn: LongMemEval-S 6 model-config runs (MiniLM, bge, arctic-xs ×2, arctic-s ×2), hard tier × 6, mini-BEIR × 6, per-encode + E2E-8 pre-swap (HARD PR2 precondition), **pre-swap winner-vs-control HNSW spot-check (Docker, production surface, same category set, its own n stated — the GATE precondition (c) producer)** (plan-review P1 fix), **2GB-VM deployment-envelope measurement: winner model load + pre-warm within the 30s-300s cold-start window AND memory smoke (`docker run --memory=2g` + winner, encode batch completes, peak RSS reported)** (plan-review P1 fix), labeled-pair calibration × 4. Encode cache model-keyed (T2); gate input manifest (T4); **provenance-writing code excludes env vars from serialization + committed artifacts scanned for key-like patterns (sk-or-, OPENROUTER) — no API-key leak into git history** (plan-review P1 fix); artifacts + `gate_1349` verdict committed; `baseline-real-minilm-2026-08-XX.json` supersedes synthetic (provenance records supersession).
**Files:**
- Commit artifacts under `tests/eval/retrieval/`, `docs/research/2026-08-17-1349-embedder-selection/`, `tools/mini_beir/results/`
- Runtime: `uv sync --extra embeddings`; Docker FalkorDB ≥4.x; benchmark box (16-32GB RAM/4-8 cores — NOT the 2GB deployment VM) + 2GB-VM-class measurement host; dev OPENROUTER_API_KEY for T5 judges (never serialized into committed artifacts).

### GATE (between PRs)
`gate_1349.py` verdict + human provenance audit. **PR2 created iff (a) PASS AND (b) user product call file = server-side AND (c) winner-vs-control HNSW spot-check (Docker, production surface — produced in T8) clears AND (d) pre-swap E2E-8 ≤300ms (winner, on the deployment VM class) AND (e) #265 merge-status check: no non-384 dimension landed before PR2 (if it did, the 768/1024 pool reopens — PR2 not created as planned).** HNSW spot-check + E2E-8 + envelope measurement are HARD PR2 pre-conditions (not post-sunk); pre-registered rollback: if the winner does not hold on the HNSW/production surface, revert to MiniLM. **UNCONDITIONAL FILING (plan-review P1 fix): the non-embedder levers research issue (key-expansion, time-aware query expansion, fusion-fix) AND the TF-IDF hard-tier lexical+semantic hybrid research issue are filed at gate completion REGARDLESS of verdict — as a NEW retrieval-optimization issue, NOT #317 (which is the reranking slice only).** If NO-WINNER/INSUFFICIENT-POWER: close with decision record + the unconditional filings attach the negative evidence.

**Ordering (restated):** if the swap passes, it lands BEFORE #317's gate-evidence production (the embedder moves first-stage ranks; both share the 300ms budget — #317 CE ≈ +210ms/top-10 CPU is incompatible with the band unless GPU/API-served).

### Task 9: `EMBEDDING_MODEL` constant + swap (PR2)

**Intent:** Single Python-side model reference (no env seam — deliberate deploy-time decision).
**Acceptance:** `EMBEDDING_MODEL = "<winner>"` at embeddings.py module top; `SentenceTransformer(EMBEDDING_MODEL)` at :108; header threshold table updated to winner bands; `tests/test_embeddings.py:269` asserts the constant; `benchmarks/run_report.py:639` imports the constant; repo-wide grep for stale all-MiniLM literals (exclude graph-scripts/bp_*.py).
**Files:**
- Modify: `tortoise/embeddings.py`, `benchmarks/run_report.py`, `tests/test_embeddings.py`, `docs/scoping/2026-08-17-1349-embedder-selection-scoping.md` (threshold table)

### Task 10: Dockerfile.hosted re-bake + CI cache (PR2)

**Intent:** The hosted image bakes the winner and fails fast if the cache is wrong; CI caches the right model.
**Acceptance:** Dockerfile.hosted bake + org-qualified FATAL path updated; post-pre-warm model-identity/degraded signal (non-blocking, /health or startup log — **tested: /health reports loaded model identity, surfaces mismatch vs EMBEDDING_MODEL; entrypoint env-reject tested: exits 1 when TORTOISE_EMBEDDER_OVERRIDE set** — plan-review P2 fixes); `.github/workflows/python-ci.yml` cache key v2 + pre-cache steps; Dockerfile.selfhost NO change (lazy download documented).
**Files:**
- Modify: `Dockerfile.hosted`, `.github/workflows/python-ci.yml`, `tortoise/hosted_api.py`
- Test: shell-level CI job (entrypoint env-reject), unit/integration (degraded-signal)

### Task 11: entrypoint.sh FATAL path (PR2)

**Acceptance:** entrypoint.sh expected-cache check updated to winner; exit-1 fast-fail preserved; **rejects `TORTOISE_EMBEDDER_OVERRIDE` env in production**.
**Files:**
- Modify: `entrypoint.sh`

### Task 12: backfill `--force-re-embed` (PR2)

**Intent:** Re-embed existing nodes across the 6-label surface (Point/Subject/Object/Document/Event/Source) — event/document ARE live vector-search surfaces.
**Acceptance:** New `--force-re-embed` flag flips the NULL-only WHERE predicates to all-rows for all 6 LABEL_CONFIG labels; **per-label text-composition ALIGNED with index-time (plan-review P1 fix): Event non-meeting = `subject + eventKind + object` (entities.py:494), Document = `title + content` (entities.py:368), AgentSession = `session_embedding_text` — NOT the backfill's current subject-only/title-only defaults; the force predicate adds `NOT (n.eventKind = 'meeting')` + non-empty-text guard so meeting events (suppressed at index time as junk vectors) are NOT backfilled**; legacy repair pass included; idempotent; `--dry-run` counts unaffected rows; **composition-parity test comparing backfill vs index-time vectors for the same Event/Document; per-label row-count completeness marker (all 6 labels re-embedded, 0 skipped by repair) recorded in the PR2 evidence — "everything moved" machine-verifiable** (plan-review P2 fixes); maintenance window + mixed-state dedup/review-connections degradation documented.
**Files:**
- Modify: `graph-scripts/backfill_embeddings.py`
- Test: `tests/test_backfill_embeddings_force.py` (incl. WHERE-predicate flip, idempotency, composition parity, meeting exclusion, per-label completeness)

### Task 13: No-DROP same-dim index rebuild (PR2)

**Intent:** Same-dim swap → NO DROP (single 384-dim HNSW auto-updates on SET — test_hnsw_vector_index.py); folded into T12/T16 per plan-review (YAGNI — documentation-only, no code/test deliverable of its own). **Acceptance folded into T16:** batching + event-log growth noted; 768-dim boundary documented (would need DROP+recreate); deploy checklist in ADR-009.

### Task 14: Threshold recalibration (PR2)

**Intent:** Model-specific cosine bands — re-derive from the labeled-pair fixture under the winner.
**Acceptance:** `tools/calibrate_thresholds.py --model <winner>` output applied to the VERIFIED cosine sites: embeddings.py (:27-28/:210/:253), cross_lens.py (:31-32 — **refactor to import the constants from embeddings.py (single source of truth, one recalibration site — plan-review good-easy fix) instead of hand-syncing duplicates**), sdk.py (:2858-2859 dedup, :5812/:5862/:5961, :7692 checkpoint assessed); **pinned-constant test fallout updated: `tests/test_cross_lens.py:198-199` (0.40/0.75), `tests/test_de2e3_content_dedup.py:16-17` (0.60/0.92), real-embedder skip-literal (:350) → winner's cache path + near-dup band assertion re-validated against the winner's distribution** (plan-review P2 fix); NOT touched: sdk.py:7399 (EP decay), :8396-8406 (RRF bands — assess separately).
**Files:**
- Modify: `tortoise/embeddings.py`, `tortoise/cross_lens.py`, `tortoise/sdk.py`
- Test: `tests/test_calibration.py` band assertions, `tests/test_cross_lens.py`, `tests/test_de2e3_content_dedup.py`

### Task 15: E2E-8 + HNSW production confirmation (PR2)

**Acceptance:** On the post-swap Docker image: E2E-8 ≤300ms p95 warm, censored arm, default query_mix (benchmarks/run_report.py); per-encode microbench (1/32 texts); winner-vs-control HNSW recall spot-check (production surface, same category set) clears the bar; provenance reads the constant; rollback = re-bake previous EMBEDDING_MODEL + force-re-embed re-run (in ADR-009).
**Files:**
- Evidence under `docs/research/2026-08-17-1349-embedder-selection/`

### Task 16: ADR-009 → Accepted + decision record finalization (PR2)

**Acceptance:** ADR-009 Status: Accepted with evidence summary (co-primary aggregates + per-category directional, hard tier, mini-BEIR, latency, calibration bands), provenance links, deploy checklist, per-label blast radius, tenant-visible changes; UX research updated; docs/00_index.md links verified.
**Files:**
- Modify: `docs/adr/ADR-009-embedder-selection.md`, `docs/research/2026-08-17-1349-embedder-selection/ux-research.md`

---

## Runtime Prerequisites

- `uv sync --extra embeddings`; branch from origin/main (worktree guard); Docker FalkorDB ≥4.x; HF cache: MiniLM present, arctic-xs/s + bge-small downloads; LongMemEval + mini-BEIR dataset caches; no API keys for gate runs (offline); dev OPENROUTER_API_KEY for T5 judges + escalation; benchmark box 16-32GB/4-8 cores; wall-clock 12-45h typical (6 configs), escalate at 5 days.
