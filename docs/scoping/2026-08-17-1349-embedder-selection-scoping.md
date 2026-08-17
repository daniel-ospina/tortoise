# Scoping — #1349: Evidence-Gated Embedder Selection for Hosted Tortoise

> issue-scoping v5.1 double diamond. Created 2026-08-17. Worktree: `/private/tmp/tw-1349` (branch `feat/1349-embedder-swap`).
> **Supersedes the original issue framing** — see §Supersession.

## Supersession (original claims disposition)

| Original claim (issue body/comment) | Disposition | Why |
|---|---|---|
| "BEIR nDCG@10 ~42→~52 (+10)" as headline gain | **Demoted to model-card context** | BEIR = doc-retrieval on web/encyclopedia corpora; target = LongMemEval + tortoise eval. BGE trained on MS MARCO (BEIR in-domain) — contamination concern. The +10 survives as an MTEB-R average (41.95 → 51.68) but is not the decision metric. |
| "zero added latency" | **Corrected: E2E-8-immaterial, not zero** | bge-small ~1.7-2× slower per encode ≈ +3-5ms absolute on sub-5ms encode — absorbed by the 300ms p95 E2E-8 verdict band (~30-60× headroom). Corpus re-embed is a one-time batch cost. |
| BEIR-18 as model-selection gate (comment) | **Replaced with mini-BEIR subset + real-model baseline** | Full 18-dataset run is disproportionate for a standard task; mini-BEIR (MS MARCO + 3-4 datasets) + in-repo hard tier + LongMemEval-relevant retrieval recall on a REAL-model baseline is the decision surface. |
| "bge-small-en-v1.5" as the swap target | **Opened to candidate pool** | arctic-xs delivers +8.2 of the +10 at identical 22M/384 size (training data, not params); arctic-s (51.98) beats bge-small (51.68) at same size. |
| "all stored embeddings re-embedded + vector index rebuilt" | **Retained** | Real requirement — backfill_embeddings.py is NULL-only; needs force-re-embed. |

## Confirmed Problem

**Select the best server-side embedding model for hosted Tortoise and land the swap, gated on pre-registered evidence: (1) produce a real all-MiniLM-L6-v2 baseline (Docker FalkorDB mode, real model + real HNSW) since the only committed baseline uses synthetic topic-centroid stand-in vectors and cannot measure an embedder swap; (2) benchmark the 384-dim CPU-feasible candidate pool — all-MiniLM-L6-v2 (control), snowflake-arctic-embed-xs, snowflake-arctic-embed-s, bge-small-en-v1.5 — on mini-BEIR + the in-repo hard tier + LongMemEval-relevant retrieval recall (R@10), with the nomic-embed-text-v1.5 / Qwen3-Embedding-0.6B / NVIDIA-Llama-3.2 class documented as the dimension-coordination upgrade path (NOT in this issue's benchmark: excluded as a **chosen scope cut** — a 768/1024-dim change is a full re-embed + index rebuild event coupled to the pending #265 encrypted-tier design and the 2GB VM feasibility budget; see the #265-status note in Why This Framing §5); (3) answer the hosted-vs-local embedding UX question as a bounded research deliverable (industry precedent: mem0/Zep/LangMem all embed server-side; local embedding is the self-hosted story) with the final product call routed to the user; (4) implement the winning swap (embeddings.py model, Dockerfile.hosted re-bake + entrypoint + pre-warm, force-re-embed tool, single Point.embedding index rebuild, threshold recalibration) ONLY IF it beats the real MiniLM baseline by a pre-registered delta on the pre-declared primary metric and E2E-8 stays ≤300ms p95; otherwise close with a documented no-swap/keep-MiniLM verdict and re-file the non-embedder levers to #317 (filed UNCONDITIONALLY — see OUT).**

## Pre-registered Decision Rule (P1 fix from problem-verify; refined in cycle 2 — mechanically executable)

**Primary metric — measurement pipeline (pinned):**
- **Source:** LongMemEval-S split (HuggingFace `xiaowu0162/longmemeval-cleaned` — verified in tools/longmem_eval/dataset.py:36), via `tools/longmem_eval` (the ONLY harness with the temporal / multi-session / single-session / knowledge-update categories; dataset.py docstring: `single-session-user|single-session-assistant|single-session-preference|temporal-reasoning|knowledge-update|multi-session`). **Category set for the primary metric (explicit filter): questions with `question_type` starting `single-session-` OR in {temporal-reasoning, knowledge-update, multi-session}; exclude `_abs` abstention questions.** Per-category breakdowns reported secondarily by the 4 paper categories (report.py PAPER_CATEGORY). The #1144 oracle/authored query sets have NO such categories (oracle = tiers easy/medium/hard; authored = domains) — "sampled per the eval's existing oracle/authored query structure" is superseded by this pin.
- **Retrieval mode:** **vector-only arm** — NEW `vector_search()` in `tools/longmem_eval/retrieve.py` that encodes the query via `EmbeddingModel` and calls `run_vector_query(proj.g, vec, ...)` directly (via `sdk._get_proj()`, already used at retrieve.py:101) + the existing annotation path. **No `tortoise_fts_query` involvement** (it is hybrid RRF-only with no vector-only mode; a fusion flag does NOT exist). Matches the MemDelta "pure embedding swap" premise. Note: in embedded CI mode the vector arm is a no-op (no model) — only meaningful in Docker mode. **Runner mode:** run via `--mock` (reader/judge bypassed) and consume ONLY `retrieval.turn_recall@k` + exported per-question outcomes (or add a named `--retrieval-only` flag).
- **Recall variant:** **turn_recall@10** as the primary metric (harness definition: fraction of `has_answer` evidence turns among the top-10 retrieved turns — multiple evidence turns per question possible, values fractional); session_recall@10 and nDCG@10/P@5 reported as secondary. Per-question export exists (run.py ~:261) — required for paired deltas.
- Rationale: the trigger is LongMemEval; the bound is recall coverage; turn-level recall is the retrieval-quality surface the swap directly moves.

**Significance test (single procedure, no disjunction):**
- Per-candidate one-sided bootstrap p-value: p = P(mean resampled paired delta ≤ 0) over per-query turn_recall@10 deltas (extension of `paired_bootstrap_ci`/`paired_deltas` in tests/eval/retrieval/bootstrap.py — **this is ~15 lines of NEW code**, expected, named here).
- **Win criterion (POWER-PRE-REGISTERED — the normative bar, supersedes the nominal +5% floor below):** candidate wins iff (a) aggregate mean turn_recall@10 beats real-MiniLM control by **≥ +5% relative** (nominal floor) AND (b) BH-FDR at q=0.10 rejects the pairwise one-sided test. **At n≈500 with per-question sd≈0.40 (the turn_recall@10 assumption), the BH-binding effective bar is ≈ +8.3-9% relative** (z ≥ 1.838 for smallest p ≤ q/m = 0.033; formula: Δ_rel ≥ z(1−q/m)·(sd/√n)/control_mean = 1.838·(0.40/√500)/0.40 ≈ 0.033/0.40 ≈ +8.3%) — **the actual pass threshold is +8-9%, not +5%**; +5% alone at this n gives ~23% power and cannot pass BH. **gate_1349.py derives the threshold from the actual per-question data and n** (formula: bar ≈ z(1−q/m)·sd/√n ÷ control_mean), unit-tested at n=200/300/500 — the +8-9% is the n≈500 instantiation, NOT a constant; as n falls (--limit subset), the bar rises (n=300 ≈ +10.6%, n=200 ≈ +13%). Paired 90% CI on the delta excluding 0 is **reported as evidence but is NOT an additional gate** — the win gate is (a) AND (b) only.
- **Multiple comparisons:** 3 candidates (arctic-xs, arctic-s, bge-small-en-v1.5) vs 1 control (MiniLM) = **3 pairwise tests**. BH at q=0.10 across 3 tests (top-rank p threshold 0.033).
- Power note (superseded by the win-criterion pre-registration above): the eval README's power math covers P@10 at n=100, NOT relative R@10 on the LongMemEval subset — the pre-registered +8-9% effective bar and its n-scaling formula are the operative thresholds; the insufficient-power outcome is the backstop for degenerate/low-n runs. **Planning instantiation assumptions:** per-question sd≈0.40 used as a proxy for the paired-delta sd; control_mean ≈ 0.37-0.40 implied by the +8.3-9% range; gate_1349.py computes the bar from the EMPIRICAL paired deltas and n (these are planning numbers only, not execution constraints).

**Secondary gates:**
- E2E-8 latency verdict ≤300ms p95 (achieved band, benchmarks/run_report.py — the `benchmarks` E2E-8, disambiguated from the tests/test_backfill_sources.py E2E-8 contract); hosted feasibility (candidate loads + pre-warms on the 2GB VM class within the existing 30s-300s cold-start window, image size documented); sentence-transformers `>=3,<6` pin compat per candidate.

**Encode protocol (pre-committed, cycle-3 corrected; cycle-5 coherence-corrected):** benchmark candidates in the config that reflects their real deployment, not a uniform handicap. **Per-candidate vendor-aware encode for the gate runs:** bge-small-en-v1.5 / MiniLM — no prefix (v1.5's designed instruction-free mode; MiniLM has no prefix concept); **snowflake arctic-xs/arctic-s — run in BOTH no-prefix AND vendor config (`prompt_name="query"`, query-side prefix, documents plain) as part of T8** (one extra encode pass per arctic model — removes the systematic false-negative risk of measuring arctic below its vendor config). The gate compares each candidate in its best-validated config; the swap lands in the config that measured best. Cross-lens matching stays plain (same single model, prefix applied query-side in the search path only, mirroring mem0's query/doc prefix tagging). This supersedes the earlier "uniform no-prefix with documented arctic penalty + conditional re-validation" framing — the arctic re-validation is now UNCONDITIONAL (both configs measured in T8, not only-if-arctic-wins).

**Outcomes:**
- **Insufficient power:** if the real-model baseline leaves no headroom (near-ceiling R@10 on the LongMemEval subset) or CIs straddle +5% → **keep MiniLM, verdict: "needs real data"** — closes cleanly, no swap. **Degenerate baseline:** if control mean turn_recall@10 < 0.05, the relative-delta win criterion is ill-defined (division by ~0) → treat as insufficient-power / needs-real-data; absolute-delta fallback fires only if a candidate clears absolute turn_recall@10 ≥ 0.30.
- **No winner:** no candidate beats control ≥Δ with FDR-clean CIs → **no swap + re-file to #317** (non-embedder levers: key-expansion, time-aware query expansion, reranking, fusion-fix) with the negative evidence attached. "No swap" and "embedder-bound → re-scope" are distinct outcomes; both named.
- **Multi-winner tie-break:** if >1 candidate clears (a)+(b), land argmax aggregate turn_recall@10; ties broken by lower E2E-8 latency, then smaller image size.
- **Vendor-config re-validation:** if an arctic winner's best config (measured in T8) does not itself clear (a)+(b) vs control, keep the config that cleared the gate, else no-swap + re-file #317.
- **BH-FDR validity note:** 3 one-sided tests vs a common control are positively dependent; BH validity requires PRDS, which plausibly holds for one-sided tests against a common control — asserted, and the gate's unit tests include a dependent-deltas case.
- **Ingest-encode note:** per-encode latency measured and reported on query + add paths (compute_embedding, sdk.py:1418) but NOT a hard gate — E2E-8 is the gate; corpus re-embed is a one-time batch cost.

## Why This Framing (evidence)

1. **The swap lever is real but bounded** — MemDelta (arXiv:2606.29914): pure embedding swap = +6.2pp LongMemEval-S (p=0.004), largest on temporal (+10.5pp) and multi-session (+11.3pp) — the issue's target categories. ⚠️ single-source preprint. Tortoise stores verbatim turns → write-side gap structurally small → retrieval-side dominant.
2. **The mechanism is misattributed** — arctic-xs (22M, fine-tuned FROM MiniLM) scores 50.15 MTEB-R vs MiniLM 41.95 = **+8.2 at identical size** — the gain is training data (hard-negative mining), not parameters. An open candidate pool is required, not a blind bge-small swap.
3. **The gates are unmeasurable as written** — verified: `baseline-embedded-2026-08-17.json` provenance = `synthetic_query_vectors: True`, `indexes.vector: False` (brute-force, redislite). The vector arm 0.8546 nDCG@10 is model-independent near-ceiling. Docker mode with real model is runnable (run.py:210-226 auto-uses EmbeddingModel; projection recomputes on write) — real baseline is step 0.
4. **Hosted-vs-local resolves as a constraint, not a blocker** — mem0 hosted = server-side baked ("We serve the model ourselves"); Zep removed its bundled local embedding service in CE; LangMem/Letta server-side. Tortoise's current bake already matches the industry hosted answer and is MORE local than mem0's default (no external embedding API call). **Tier-partitioned:** #265 (encryption epic) is **PENDING-MERGE, NOT SHIPPED** (verified: zero `encryptionVersion` hits in tortoise/ code — it is a scoping artifact with its client-embedding path listed "pending merge" and the model-version pin as a P2 design item). Its proposed design pins client embeddings to MiniLM/384 for `encryptionVersion>=1` teams. The open question is only the DEFAULT tier → user product call at Phase 8.
5. **Cross-issue constraint (#265 encrypted tier) — STATUS-CORRECTED (coherence check):** the 384-dim scope restriction is a **CHOSEN scope cut, not a forced shipped constraint** — #265 has not shipped, so there is no in-code dimension pin today. The restriction rests on: (a) the pending #265 design (encrypted teams would ship client-computed 384-dim MiniLM vectors into a shared index — if that lands before/around a swap, staying 384-dim avoids a coordinated dimension migration); (b) the 2GB VM feasibility budget (768/1024-dim candidates are materially heavier); (c) single `Point.embedding` index dimension-fixedness (any dim change = full re-embed + index rebuild event). **Escape clause (pre-registered): if #265 lands at a different dimension before PR2, the 768/1024 pool reopens** (falsification check below). **Semantic-space note:** dimension equality ≠ encoder-space compatibility — if default and encrypted tenants share a DB/namespace, a non-MiniLM-family server encoder mixes two distributions in one index. "Family-preserving" (arctic-xs fine-tuned FROM MiniLM) is a HYPOTHESIS, not a verified guarantee — treat as a tiebreak consideration, not a ranked preference.
6. **Latency is a real gate, correctly defined** — verdict-band rule (user-confirmed): E2E-8 ≤300ms p95; bge-small's ~1.7-2× encode slowdown is E2E-8-immaterial (≈3-5ms on the query path).

### Rejected alternatives

- **Framing 1 ("which levers move LongMemEval?") as the deliverable** — rejected for scope: unbounded retrieval-research agenda (key-expansion, time-aware query, reading strategy); cannot be a standard-complexity atomic deliverable. Its insight (measure on the right benchmark) is absorbed; its agenda is filed out to #317.
- **Framing 2 ("how do hosted customers get quality embeddings?") as the deliverable** — rejected as the code task: the current bake already matches the industry hosted answer; the swap is architecture-neutral. Retained as a bounded research deliverable (mem0/Zep/LangMem precedent) + user product call. The factual premise of "self-hosted = local" was corrected: mem0 self-hosted ALSO defaults server-side (OpenAI), local = opt-in.
- **Framing 3 as written (unconditional bge-small swap)** — rejected: unmeasurable gates (synthetic baseline), misattributed mechanism (training data not params), false zero-latency, preempts the candidate research the user requested. Its terminal action (the swap) survives behind the evidence gate.
- **4th framing ("measurement-first: the missing baseline")** — rejected: mistakes the instrument for the goal; the user's explicit intent is a decision, and a baseline-only issue leaves the model + UX questions unanswered. Baseline-building is an in-scope deliverable instead.

## Falsification Check

This definition is wrong if:
- All candidates ≈ MiniLM within noise on the real baseline (mini-BEIR + hard tier + LongMemEval R@10) → verdict flips to keep-MiniLM + re-file #317.
- E2E-8 p95 > 300ms post-swap → verdict band vetoes the swap.
- MemDelta replication on tortoise's actual pipeline shows the retrieval gap is NOT embedding-bound (recall coverage/reading dominates) → the benchmark still produces negative evidence and redirects.
- The user product-calls hosted-vs-local differently (hosted customers must embed locally as a differentiator) → the candidate class changes entirely (configurable/local embedding support) and the swap becomes moot until architecture resolves.
- #265 encrypted tier ships a dimension change → shared-index constraint dissolves and the 768/1024 pool reopens.

**Confidence: 80.**

## Scope Guardrails

### IN (atomic deliverable — standard task)
1. Real all-MiniLM-L6-v2 baseline via #1144 Docker mode (in-scope step 0 — the swap's gates are unmeasurable without it; data is wipeable per user).
2. Candidate benchmark: MiniLM (control) + arctic-xs + arctic-s + bge-small-en-v1.5 on mini-BEIR (**pinned: MS MARCO dev qrels-constrained top-1000 subset + NFCorpus + SciFact + FiQA** — small, stdlib-fetchable, low-contamination; expected download/encode budget documented before execution on the 2GB VM class) + in-repo hard tier + LongMemEval-relevant turn_recall@10 (vector-only arm) — via the LongMemEval-S runner (tools/longmem_eval) + the #1144 eval metrics. LongMemEval gate = retrieval recall (gold evidence in top-k), NOT the expensive end-to-end judged runner (that's post-swap for the winner only). mini-BEIR is a research surface, NOT a gate — the decision rule gates only on turn_recall@10 + E2E-8. **Wall-clock budget (unified): 8-30h typical (0.3-1.25 days), up to 4 days with contention; escalate to project-workflow if wall-clock exceeds 5 days.**
3. Hosted-vs-local research deliverable: mem0/Zep/LangMem/Letta precedent + tier partition (#265) + recommendation; final product call routed to user.
4. Pre-registered decision rule (above): primary metric turn_recall@10, **nominal +5% relative floor AND BH-FDR q=0.10 (effective n-adaptive bar ≈ +8-9% at n≈500, derived by gate_1349.py from actual data — see rule)**, paired 90% CIs reported as evidence only (NOT a gate), E2E-8 ≤300ms, feasibility gate.
5. Conditional swap implementation (only if gates clear): embeddings.py model swap (384-dim), Dockerfile.hosted re-bake + entrypoint.sh cache path + hosted_api.py pre-warm, force-re-embed flag on backfill_embeddings.py (currently NULL-only), single Point.embedding index rebuild (**same-dim, NO-DROP — FalkorDB auto-updates on SET; drop+recreate reserved for the 768-dim future pool only**), threshold recalibration (**labeled-pair set = net-new named work, capped ≤100-200 pairs with a per-band floor (≥30 pairs per target band: cross-vocab 0.35-0.51, near-dup 0.75+, unrelated noise — the two bands the swap most directly moves are prioritized; the sdk dedup 0.60/0.92 and checkpoint 0.95 constants are assessed-not-recalibrated unless the fixture shows model sensitivity)**), per-encode + E2E-8 latency measurement, decision record (model choice + location answer + threshold values).
6. Supersession block + this doc as the authoritative scoping record.

### OUT (filed as separate issues)
- **Hosted-vs-local product decision** (default-tier embedding location) → user decision at Phase 8; if "customer-local for hosted" wins, project-level issue (mixed vector spaces, model-version pin, SDK work; coordinate with #265).
- **LongMemEval non-embedder levers** (key-expansion, time-aware query expansion, reranking, fusion-fix — fused 0.835 < vector 0.855) → **filed UNCONDITIONALLY as a research issue** (the research's own evidence: beyond ~95% R@5 the bottleneck is the reader; Hindsight wins on commodity embeddings + hybrid/reflection — these levers are independent of the swap outcome and must not evaporate on a PASS verdict).
- **TF-IDF hard-tier lexical advantage** (0.599 > vector 0.530 — hypothesis-generating, needs real-model confirmation) → filed as a separate lexical+semantic hybrid research issue regardless of gate result.
- **768/1024-dim upgrade** (nomic-v1.5, Qwen3-Embedding-0.6B, NVIDIA Llama-3.2 fine-tunes) → separate issue; reopens automatically if #265 lands at a non-384 dimension before PR2 (escape clause); requires hosted VM feasibility (Qwen3-0.6B ≈ 1.2GB vs 90MB baked; 2GB VM cold-start #545).
- **Full-BEIR/MTEB validation** of the eventual winner → out unless mini-BEIR flags contamination risk.
- **E2E-8 post-swap confirmation** — T15 remains a post-merge confirmation, but a **pre-swap E2E-8-with-candidate check (T3 `--model`) is a HARD PR2 pre-condition** (coherence-check fix: the latency gate must not be first-measured post-sunk).

## Wiring Check (touch points)

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| tortoise/embeddings.py (model + thresholds) | Code | IN step 5 | ✅ |
| Dockerfile.hosted (Layer 1 bake, ~90MB) + entrypoint.sh (FATAL cache check) + hosted_api.py pre-warm | Deploy | IN step 5 | ✅ |
| tortoise/search_engine.py (run_vector_query 384-dim contract, docstring) | Code | IN step 5 | ✅ |
| tortoise/projection/__init__.py (single Point.embedding vector index, 384 hardcoded) | Code | IN step 5 | ✅ |
| graph-scripts/backfill_embeddings.py (NULL-only → force flag) | Tooling | IN step 5 | ✅ |
| tests/eval/retrieval/ (#1144 eval, Docker mode, baseline) | Eval | IN steps 1-2 | ✅ |
| benchmarks/ (E2E-8 latency verdict) | Benchmark | IN step 4 | ✅ |
| #1144 eval (quality delta) | Dependency | ✅ (on main) | ✅ |
| #316 benchmark (latency) | Dependency | ✅ (CLOSED, infra on main) | ✅ |
| #265 encrypted tier (client embeddings, 384 pin, shared index) | Constraint | OUT → cross-issue note; semantic-space note below | ✅ |
| LongMemEval-S dataset (HF xiaowu0162/longmemeval-cleaned) | Data | ✅ IN (download + cache; wipe + re-run user-confirmed) | ✅ |
| mini-BEIR harness (MS MARCO dev + 3 datasets, stdlib-download + in-repo scorer per longmem_eval urllib precedent — no new top-level deps) | Eval | ✅ IN step 2 (net-new harness work, named) | ✅ |
| bootstrap.py p-value extension (one-sided bootstrap p + BH-FDR) | Eval | ✅ IN (expected ~15 lines new code) | ✅ |
| tools/longmem_eval/retrieve.py vector-only arm | Eval | ✅ IN (extend) | ✅ |
| Self-hosted installs (Dockerfile.selfhost, first-use download 90MB→127MB) | Stakeholder | IN (note: chosen default propagates; MiniLM documented fallback) | ✅ |
| CI (no model — TF-IDF fallback; swap invisible) | Regression surface | IN (threshold/dim regression covered by manual Docker eval) | ✅ |

## Solution Approach (Phase 4-5 — Approach B: Two-PR, harness/evidence first, conditional swap second)

**Selected: Approach B.** PR1 (unconditional): benchmark tooling + ALL evidence on main, zero production-code changes. PR2 (conditional, created iff `gate_1349.py` verdict = PASS): mechanical swap. Gate structurally enforced — PR2's existence IS the gate output; fail path = zero-code-change retention (no revert, no edit-stripping, and crucially **no data mutation** — re-embedded vectors only ever land if the swap lands).

Rejected: A (config-driven single PR + `TORTOISE_EMBEDDING_MODEL` env seam) — env toggle is architecturally incompatible with the fixed 384-dim index + single-model bake + `HF_HUB_OFFLINE=1` (runtime toggle would need both models baked; wrong-dim runtime toggle = silent retrieval breakage); evidence entangled with swap incentivizes gaming; tips to Complex. C (throwaway-branch literal edits) — run-log provenance unauditable under mandatory review gates.

**Harness injection mechanism (P1 fix, solution-verify cycle 1; mechanism pinned cycle 2):** benchmark-only override honored BEFORE first `EmbeddingModel.get()` — `tools/embedder_probe.py` argv-selects the model and **monkeypatches the `SentenceTransformer` symbol inside `sentence_transformers`** (the mechanism already proven at tests/test_embeddings.py:258-269 — zero production-code changes, preserving PR1's "zero behavioral changes" invariant; `EmbeddingModel._MODEL_CLASS` does NOT exist and is NOT added in PR1), then calls `EmbeddingModel._reset()` before the first `get()` (class-singleton cooldown at embeddings.py:51-88). If an env var is used it is named **`TORTOISE_EMBEDDER_OVERRIDE`**; T11's entrypoint FATAL rejects it in the hosted image. Asserted 384-dim + non-None with HARD FAIL (never silent TF-IDF degrade when `--model` requested).

### PR1 tasks (unconditional — tooling + evidence, zero prod changes)
- **T1 `tools/embedder_probe.py`** — candidate-model injection via the monkeypatch seam above, `reset()`, `PROBE_MODELS` registry: minilm/arctic-xs/arctic-s/bge-small → HF ids. No-prefix is the deliberate benchmark condition (rule-compliant); `prompt_name` threaded for the arctic vendor-config re-validation.
- **T2 LongMemEval vector-only arm** — `vector_search()` in `tools/longmem_eval/retrieve.py`: encode query via injected model, `run_vector_query(proj.g, qvec, limit, is_embedded=getattr(proj, '_is_embedded', True), vector_index_api=getattr(proj, '_vector_index_api', None))`, NEVER `tortoise_fts_query`. **Retrieval geometry stated (coherence-check fix):** the per-question LongMemEval graphs are embedded redislite → the vector arm runs BRUTE-FORCE exact cosine in the gate runs (NOT HNSW); production lands on HNSW approximate. **Mitigation: a post-swap recall spot-check on the production HNSW surface (winner vs control, Docker mode, same category set) is added to T15** — the gate decides on exact search, the spot-check confirms the winner holds under approximate search. The "embedded CI no-op" note in the pre-registered rule refers to CI (no model loaded → arm no-ops there); in the Docker-mode gate runs the arm IS live brute-force. `--retriever {hybrid,vector}` (default hybrid, backward-compat), `--model`, `--retrieval-only` (skips reader/judge — immune to contamination), per-model checkpoint keying `{retriever}__{model}__{prompt}`. **Mid-run encode-degrade = HARD FAIL** (degraded flag / None vector → abort model run with distinct exit code + `MODEL_ENCODE_FAILED` marker so gate refuses that run).
- **T3 eval + benchmark parameterization** — `tests/eval/retrieval/run.py` `--model`/`--query-prompt`; `benchmarks/run_report.py` gains `--model` override + reads provenance from the actual singleton/constant (P1 fix: E2E-8 must be measurable PRE-swap).
- **T4 `bootstrap.py` one-sided p + BH-FDR + `gate_1349.py`** — net-new (existing functions are percentile-CI, different philosophy — #1144's absolute-band SHIP/WARN/BLOCK gate must not be conflated). Gate implements the FULL pre-registered rule incl. the n-adaptive power bar (derived from actual per-question data: bar ≈ z(1−q/m)·sd/√n ÷ control_mean; unit-tested at n=200/300/500 + degenerate control-mean + multi-winner cases). **CHECKPOINT (pre-burn gate validation): `gate_1349.py` unit tests on synthetic per-question data with known verdicts (PASS/NO-WINNER/INSUFFICIENT-POWER) run and reviewed BEFORE T8 evidence production — a mis-implemented gate must not burn the 8-30h evidence budget.** Denominator = all filtered-split questions (non-evidence 0/0 tied deltas dilute power — documented). Multi-winner: argmax aggregate turn_recall@10, then E2E-8 latency, then model size. Absolute-fallback: control mean < 0.05 → candidate ≥ 0.30 absolute. mini-BEIR + hard tier = **informational, NOT gating** (documented; feed tiebreak + monitoring baseline).
- **T5 labeled-pair fixture + calibration** — `tests/fixtures/labeled_pairs.jsonl` ≤200 pairs, density-weighted toward the calibration-relevant bands (0.35-0.51 cross-vocab, 0.75+ near-dup), two independent LLM judges via existing `tools/judge_harness.py` + `tools/kappa.py` κ≥0.60 semantics, owner adjudication; `tools/calibrate_thresholds.py --model <c>`. MiniLM run must reproduce #399 bands (sanity).
- **T6 mini-BEIR harness (research surface, NOT gate)** — MS MARCO dev top-1000 q / 100k-passage sampled corpus (documented NOT leaderboard-comparable) + NFCorpus + SciFact + FiQA; **BEIR raw tsv.gz/jsonl via urllib (NO parquet — zero new deps)**, in-repo nDCG@10/R@10 scorer. **Contamination note:** arctic/bge are trained on MS MARCO — treat MS MARCO as in-domain sanity, weight the 3 OOD datasets for the selection read.
- **T7 UX research + ADR-009 (Pending evidence) + docs registration** — hosted-vs-local analysis (mem0/Zep/LangMem server-side precedent; swap invisible to tenants; self-hosted no-bake note: first boot after swap triggers a one-time runtime model download — document size/time); decision record ships in PR1 regardless of gate outcome.
- **T8 evidence production** — real-MiniLM Docker baseline (supersedes synthetic `baseline-embedded-2026-08-17.json`), LongMemEval-S × 4 (vector arm; **arctic runs BOTH no-prefix AND `prompt_name="query"` vendor config — 5 model-config runs total, unconditional**), hard tier × 4 (5 with arctic vendor config), mini-BEIR × 4 (5 with arctic vendor config), per-encode + E2E-8 (via T3 `--model` — **pre-swap E2E-8-with-candidate is a HARD PR2 pre-condition**, not a post-sunk discovery), labeled-pair calibration × 4. **Wall-clock pre-registered:** 500Q × ~115k-token haystacks × 4 models ≈ 8-30h on the 2GB VM; cross-question encode caching (overlapping haystack content is re-ingested per question — cache encodes); slow-model subset fallback (`--limit`) with gate power stated for that n; artifacts + `gate_1349` verdict committed. **Arctic both-config measurement scheduled here (UNCONDITIONAL — coherence-check fix):** no-prefix AND vendor config both measured in the gate runs; the swap lands in the config that measured best.

### Gate (between PRs)
`gate_1349.py` on committed reports → PASS(model) / NO-WINNER / INSUFFICIENT-POWER. Human provenance audit (reports diffed vs committed artifacts, no cherry-picking). **PR2 created iff PASS AND the user product call confirms server-side default embedding** (Phase 8 hosted-vs-local decision is a gate input, reviewed during PR1 — if the user product-calls customer-local-for-hosted, the swap is moot even on PASS per the falsification check).

### PR2 tasks (conditional — the swap)
- **T9 `EMBEDDING_MODEL` constant + swap** — module constant (NO env seam in production), `embeddings.py:108` flip; header threshold table updated; **`tests/test_embeddings.py:269` asserts the constant** (P1 fix — breaks on rename otherwise); `benchmarks/run_report.py:639` reads the constant (provenance must not lie post-swap); repo-wide grep for stale `all-MiniLM-L6-v2` literals (docstrings sdk.py:490, embeddings.py header, data/embedding-retrieval.md fastembed mention — fix opportunistically; **EXCLUDE graph-scripts/bp_*.py — historical decision-record scripts for the #399 model choice, do NOT rewrite**).
- **T10 Dockerfile.hosted re-bake** — model-cache bake + org-qualified FATAL path (`models--snowflake--...` / `models--BAAI--...`), CI cache key v1→v2 + pre-cache steps; Dockerfile.selfhost = NO change (runtime download, documented).
- **T11 entrypoint.sh FATAL** — expected-cache check updated + **rejects the benchmark override env var in production**.
- **T12 `backfill_embeddings.py --force-re-embed`** — **ALL embedding-bearing labels: Point, Subject, Object, Document, Event** (entities.py:171/284/331/405/528 — re-verified cycle 2; **Source and Operator write NO embeddings**: Source MERGE has no embedding field, Operator = Points with `is_operator=true` covered via Point; the search API accepts entity_type for all seven but only these five store vectors) — P1 fix: event/document ARE live vector-search surfaces via session continuity `entity_type='event'`; same no-DROP SET mechanics (auto-update verified), direct-Cypher path (no PointRevised clobber), idempotent, `--dry-run`, `--all-tenants`; maintenance window + mixed-state ranking acceptance documented. **T16 blast radius uses this exact five-label list.**
- **T13 no-DROP same-dim rebuild** — auto-updates on SET (test_hnsw_vector_index.py); batching + event-log growth noted; 768-dim boundary documented (would need DROP+recreate — future pool extension).
- **T14 threshold recalibration** — VERIFIED cosine sites only (re-verified cycle 2): `cross_lens.py:31-32` (0.40/0.75), `embeddings.py:27-28/210/253` (0.40/0.75/0.3), `sdk.py:2858-2859` (`DEDUP_REVIEW_THRESHOLD=0.60`/`DEDUP_AUTO_MERGE_THRESHOLD=0.92` — dedup cosine constants; the 2670-2671 references were IMPL query lines, corrected), `sdk.py:5812/5862/5961` (0.40 cosine defaults: review_connections, get_cross_lens_candidates #399-calibrated), `sdk.py:7692` (`checkpoint()` semantic-dedup cosine default 0.95 — assess against the winner's near-dup band; likely band-robust but verify, not assume). **NOT touched:** sdk.py:8396-8406 (RRF score band-normalization 0.3/0.5 — operates on RRF scores, not cosine; decide separately if score-distribution-sensitive), sdk.py:7399 (EP recency decay 0.95 — unrelated, untouched).
- **T15 E2E-8 ≤300ms on post-swap image** — defined exactly: p95 warm, censored arm, default query_mix, `benchmarks/run_report.py`; per-encode microbench (1/32 texts) added for future rotation decisions; provenance reads the constant.
- **T16 ADR-009 → Accepted** — evidence summary, per-label blast radius (P1 fix: state swap impact per label), deploy checklist, UX final statement.

### Wiring table (solution)

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| tortoise/embeddings.py (:108 literal, :210/:253 thresholds, header) | prod code | T9, T14 | ✅ |
| tortoise/cross_lens.py (:32 duplicate threshold) | prod code | T14 | ✅ |
| tortoise/sdk.py query-path encode (:8718) | prod code | T15 | ✅ |
| tortoise/sdk.py cosine thresholds (:2858-2859 dedup 0.60/0.92, :5812/:5862/:5961 0.40, :7692 checkpoint 0.95) | prod code | T14 | ✅ |
| tortoise/search_engine.py (:340 384 contract, run_vector_query) | read-only dep | T2; docstring → constant | ✅ |
| Five-label embedding surface (entities.py:171/284/331/405/528) | prod data | T12 (P1 fix) | ✅ |
| Dockerfile.hosted / entrypoint.sh | deploy | T10/T11 | ✅ |
| hosted_api.py pre-warm | prod code | model-agnostic — no change | ✅ |
| python-ci.yml cache | tooling | T10 (v2) | ✅ |
| graph-scripts/backfill_embeddings.py | ops | T12 | ✅ |
| tools/longmem_eval/ | eval tooling | T2 (on main already) | ✅ |
| tests/eval/retrieval/ | eval tooling | T3/T4/T8 | ✅ |
| tests/test_embeddings.py:269 | test | T9 (assert constant) | ✅ |
| benchmarks/ (run_report :639, synthetic_corpus :29) | benchmark | T3/T9/T15 | ✅ |
| docs/ (ADR-009, research, 00_index, embedding-retrieval.md) | docs | T7/T16 | ✅ |
| Dockerfile.selfhost | deploy | no change (runtime download documented) | ✅ |
| #265 encrypted tier | future constraint | NOT in code (pending-merge design); 384-dim = chosen scope cut + escape clause; suppress_embedding seam is the #900 doc-path flag (NOT a #265 seam — corrected) | ✅ |

### Runtime prerequisites
- `uv sync --extra embeddings`; branch from **origin/main** (local main 237 commits stale — worktree guard); Docker FalkorDB ≥4.x for authoritative baseline/E2E-8; HF cache: MiniLM present, arctic-xs/s + bge-small downloads (~100-200MB); LongMemEval + mini-BEIR dataset caches; no API keys (gate runs offline `--retrieval-only`); ~16-32GB RAM, 4-8 cores; **wall-clock unified: 8-30h typical (0.3-1.25 days), up to 4 days with contention, escalate at 5 days** (pre-registered).

### solution-verify — Cycle 1
- Verifier A: P0=0, P1=1 (five-label surface), P2=4, P3=2; Verifier B: P0=0, P1=4, P2=6, P3=1
- Controller action: FIXED 6 P1 groups — (1) five-label embedding surface → T12 extends backfill to Point/Subject/Object/Document/Event + ADR per-label blast radius; (2) harness injection mechanism pinned (monkeypatch seam, no prod changes); (3) gate power math pre-registered (+8-9% effective bar at n≈500); (4) E2E-8 measurable pre-swap via run_report --model; (5) T8 wall-clock + subset fallback + encode caching; (6) test_embeddings.py:269 asserts constant. Incorporated P2s: is_embedded derivation, encode-degrade HARD FAIL, provenance literals read constant, T14 site corrections, parquet-forbidden mini-BEIR, contamination note, kappa/judge-harness reuse.
### solution-verify — Cycle 2
- Verifier A: P0=0, P1=1 (T14 site list), P2=3, P3=2; Verifier B: P0=0, P1=1 (power-math fix only in T4 not the normative rule), P2=5, P3=1
- Controller action: FIXED both P1s — (1) normative Pre-registered Decision Rule now carries the derived n-adaptive power bar (formula bar ≈ z(1−q/m)·sd/√n ÷ control_mean; +8-9% at n≈500, n=300 ≈ +10.6%, n=200 ≈ +13%; gate derives from actual data, unit-tested at n=200/300/500); (2) T14 site list re-verified against code (2858-2859 dedup constants corrected, 7692 checkpoint 0.95 assessed, RRF bands at 8396-8406 excluded correctly). Incorporated P2s: T12 label enumeration corrected (Source/Operator write no embeddings), injection mechanism pinned (monkeypatch + named TORTOISE_EMBEDDER_OVERRIDE env for T11 FATAL), gate pre-burn checkpoint added (synthetic-verdict unit tests before T8), PR2 precondition includes the user product call, wall-clock unified (8-30h typical / 4 days contention / 5-day escalation).

### solution-verify — Cycle 3
- Verifier A: P0=0, P1=0, P2=1 (stale wiring-table sdk.py row), P3=4; Verifier B: P0=0, P1=1 (complexity rating unreconciled with HIGH library-deps axis), P2=3, P3=2
- Controller action (tiebreaker): FIXED the P1 via explicit rating reconciliation (kept standard with written justification: HIGH axis conditional-isolated — PR1 zero prod deps, new dep class lands only in gate-gated PR2 with pre-verified compat; one atomic deliverable no MECE decomposition; bounded risk surfaces incl. 5-day escalation + gate pre-burn checkpoint; route-integrity note: escalate if plan-verify/implementation reveals project-scale needs). Incorporated P2s: IN-4 decision-rule summary rewritten to the n-adaptive bar + CI-as-evidence, wiring-table sdk.py row corrected to :2858-2859/:5812/:5862/:5961/:7692, IN-2 wall-clock unified (8-30h typical / 4d contention / 5d escalate). P3s: power-note planning assumptions stated (sd≈0.40 proxy, control_mean 0.37-0.40, empirical-derivation note), T9 grep excludes graph-scripts/bp_*.py (historical decision records).

### Second-model coherence check (Phase 5.6 — deepseek-v4-pro)
- Verdict: PASS with 2 P1s — controller FIXED both: (1) **#265 status corrected** — pending-merge, NOT shipped (zero encryptionVersion hits in code); 384-dim restriction reframed as a CHOSEN scope cut (pending #265 design + 2GB VM + single-index dimension-fixedness) with a pre-registered escape clause (if #265 lands non-384 before PR2, the 768/1024 pool reopens); (2) **arctic encode made symmetric** — arctic-xs/arctic-s run in BOTH no-prefix AND vendor config (`prompt_name="query"`) UNCONDITIONALLY in T8 (5 model-config runs), replacing the biased no-prefix + conditional re-validation. Incorporated P2s: non-embedder levers + TF-IDF-hard-tier findings filed UNCONDITIONALLY (independent of gate result — not only on NO-WINNER); pre-swap E2E-8-with-candidate made a HARD PR2 pre-condition (latency not first-measured post-sunk); retrieval-geometry statement (gate = brute-force exact cosine on embedded graphs, post-swap HNSW recall spot-check added to T15, CI-no-op vs gate-runs distinction resolved). P3s: suppress_embedding seam relabeled as the #900 doc-path flag (not #265); labeled-pair per-band floor ≥30 pairs + assess-not-recalibrate for sdk dedup/checkpoint constants; BH-FDR PRDS note + dependent-deltas unit test; Docker-availability fallback noted (5-day escalation already covers). "Family-preserving" downgraded from ranked preference to tiebreak hypothesis.

## Review Cycle Log

### problem-verify — Cycle 2
- Verifier A: P0=0, P1=2, P2=3, P3=2; Verifier B: P0=0, P1=1, P2=3, P3=3
- Controller action: FIXED both P1s — (1) measurement pipeline pinned: source = LongMemEval-S (HF longmemeval-cleaned, only harness with the categories), retrieval mode = vector-only arm (extend tools/longmem_eval/retrieve.py, matches MemDelta pure-swap premise), recall variant = turn_recall@10 primary; (2) inference protocol made mechanically executable: significance = one-sided bootstrap p (p = P(mean resampled delta ≤ 0), ~15 lines new bootstrap.py code, named), win = ≥+5% RELATIVE to control mean (aggregate, not per-query) AND BH-FDR q=0.10 over 3 pairwise tests (count corrected 4→3), single paired-CI procedure (OR-disjunction removed), encode protocol pre-committed (no instruction prefix). Incorporated P2/P3: relative-delta zero-base rule, mini-BEIR harness named (stdlib-download, no new deps), #265 semantic-space divergence (arctic-xs family-preserving preference + tenant-isolation assumption), research-doc stale passages corrected (per-label index claim, synthesis candidate list, false-economy line, Qwen3 dim phrasing), power-math caveat (P@10 n=100 ≠ relative R@10).
- Cycle 1 (for the record):
  - Verifier A: P0=0, P1=1, P2=4, P3=3; Verifier B: P0=0, P1=5, P2=4, P3=5
- Verifier A: P0=0, P1=1, P2=4, P3=3
- Verifier B: P0=0, P1=5, P2=4, P3=5
- Controller action: FIXED all 6 P1 groups —
  1. **Pre-registered decision rule added** (primary metric R@10, +5% relative delta, FDR 10% across 4 pairwise comparisons, paired 90% bootstrap CIs, insufficient-power and no-winner outcomes, E2E-8 disambiguated to benchmarks/run_report.py) [A P1, B P1]
  2. **A/B split on hosted-vs-local resolved toward B (IN)** — user flagged CRITICAL; retained as bounded research deliverable + user product call at Phase 8; competitor precedent reframed as feasibility evidence, not an agent-synthesized product verdict [B P1]
  3. **#265 encrypted-tier collision surfaced** — shared 384-dim index + pinned client embeddings → 384-dim constraint retained for THIS issue, 768/1024 pool filed out with cross-issue coordination note [B P1]
  4. **Research artifact committed** to feat/1349-embedder-swap (was untracked) [B P1]
  5. **Candidate feasibility gate added** — 2GB VM cold-start budget (Qwen3-0.6B ≈ 1.2GB excluded from benchmark), sentence-transformers <6 pin compat checked per candidate [B P1]
  6. **In-repo hard-tier TF-IDF finding reframed** as hypothesis-generating (synthetic-vector source; needs real-model confirmation) — dropped the "= LongMemEval profile" equivalence [A P2 → incorporated]
- Incorporated P2/P3: ingest-encode latency note (A P2), Docker re-bake surface named (A P2), conditional outcome re-file to #317 (B P2), single-Point-index correction — verified code creates vector index only on Point.embedding, not six per-label indexes (B P2 — this STRENGTHENS the 384-dim case: one index, dimension-fixed), LongMemEval gate pinned to retrieval recall R@10 not end-to-end judged (B P2), supersession block (B P2), self-hosted stakeholder note (B P3), metric labels normalized to MTEB Retrieval avg (A P3), Qwen3 native-dim correction (1024, MRL 768) + instruction-prefix note (B P3), entity-bound → mapped to information-extraction category (A P3).
- Re-dispatching both verifiers with the fixed problem definition...

## Complexity

| Domain | Rating |
|--------|--------|
| Overall | **standard** (Level: task — one atomic evidence-gated decision + conditional swap, delivered as two PRs) |
| Library-deps | high (new model dep, version pins, sentence-transformers compat) |
| Architecture | medium (single index rebuild, thresholds, Docker bake) |
| UX | medium (hosted-vs-local research + product call) |
| Ontology | low (no schema/entity changes; embedding property unchanged) |

**Rating reconciliation (solution-verify cycle 3, P1 fix — why standard despite the HIGH library-deps axis):**
- **The HIGH axis is conditional-isolated.** PR1 (unconditional) touches ZERO production dependencies — it is pure additive tooling (probe, vector arm, gate script, fixtures, docs). The new model dependency class lands ONLY in PR2, which is created iff the pre-registered gate PASSES, with all four candidates pre-verified sentence-transformers-compatible (`>=3,<6` pin, no `trust_remote_code`). No production code path ever loads an unverified model.
- **One atomic deliverable, no MECE child-issue decomposition.** The issue-workflow escalation clause ("wiring or E2E → project-workflow") is not triggered: the wiring table is the integration-surface ACCOUNTING every task produces, not a decomposition; the E2E-8 check is a latency verification of one code path, not an E2E test-suite deliverable; there are no child issues. The two-PR structure is acknowledged as **phased delivery** (gate verdict + user product call between PRs) — the standard rating rests on PR1's zero-prod-deps isolation, the gate-gated PR2 with pre-verified compat, the zero-data-mutation fail path, and the mechanical escalation triggers, not on "commit granularity".
- **Bounded risk surfaces.** The 8-30h evidence burn is pre-registered with a 5-day wall-clock escalation trigger to project-workflow; the gate pre-burn checkpoint (T4) validates gate_1349.py on synthetic data before the burn; the fail path is zero-code-change retention (no data mutation).
- **Route-integrity note:** if plan-verify (next gate) or implementation reveals genuine project-scale decomposition (e.g., the evidence phase needs its own child issues, or the user product call forces a project-level hosted-vs-local architecture change), escalate to project-workflow then. This scoping doc remains the authoritative record either way.

## Clarifications

*(from issue-pre round — 2026-08-17)*

| Question | Answer | How |
|---|---|---|
| Prod data migration scope | No production data (pre-launch); data freely wipeable; LongMemEval graph data wiped for re-runs anyway | chosen (user) |
| BEIR gate scope | Mini-BEIR subset first (MS MARCO + few datasets) + broader model research incl. "new meta ones"; CRITICAL hosted-vs-local UX question (mem0 hosted tier) | chosen (user) |
| Latency acceptance rule | Verdict-band — proceed if E2E-8 ≤300ms achieved band | chosen (user) |
| Model candidates | Open pool: MiniLM control + arctic-xs + arctic-s + bge-small + (nomic/Qwen3 as documented upgrade path); Meta/NVIDIA = GPU/API tier context | resolved by research |
| mem0 hosted behavior | Server-side baked embeddings; OSS self-hosted defaults server-side too (local = opt-in); tortoise bake MORE local than mem0 default | resolved by research |

### Deferred to Research
*(researchable questions — answered in Phase 1.5)*

- Best 2026 small-CPU embedder class for memory retrieval (answered: Qwen3-0.6B accuracy leader but 768/1024-dim → upgrade path; arctic/bge = 384-dim CPU fit) *(Impact: 9 | Uncertainty: 6)*
- FalkorDB vector index rebuild mechanics for a dim change (answered: drop+recreate per index; dim not in-place; one Point index here) *(Impact: 7 | Uncertainty: 6)*
- Threshold calibration protocol per model (answered: labeled pair sweep; bands non-portable) *(Impact: 7 | Uncertainty: 6)*
- Hosted-vs-local embedding UX across competitors (answered: mem0/Zep/LangMem server-side; Zep removed local CE option; BYO-vectors = Zep manual mode only) *(Impact: 8 | Uncertainty: 5)*

## External Research (Phase 1.5 artifact)
See `docs/research/2026-08-17-1349-embedder-selection.md` (committed): Axis 1 model selection, Axis 2 architecture, Axis 3 hosted-vs-local — all with source tags + URLs + pitfalls.

### Integration Docs
- **New deps:** one embedding model swap within existing `[embeddings]` extra (sentence-transformers already pinned `>=3,<6`; all four candidates load via SentenceTransformer — bge-small-en-v1.5, snowflake/snowflake-arctic-embed-xs, snowflake/snowflake-arctic-embed-s verified sentence-transformers-compatible; nomic needs `trust_remote_code` + extra deps — excluded from benchmark, upgrade path only). No new top-level deps.
- **Version pins:** model name becomes a single documented constant in embeddings.py; Dockerfile.hosted Layer-1 bake cache key changes with model; entrypoint.sh FATAL check path updates.
- **API surface:** none (embeddings.py internal; no external API calls — embeddings are fully local).
