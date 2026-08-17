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

**Select the best server-side embedding model for hosted Tortoise and land the swap, gated on pre-registered evidence: (1) produce a real all-MiniLM-L6-v2 baseline (Docker FalkorDB mode, real model + real HNSW) since the only committed baseline uses synthetic topic-centroid stand-in vectors and cannot measure an embedder swap; (2) benchmark the 384-dim CPU-feasible candidate pool — all-MiniLM-L6-v2 (control), snowflake-arctic-embed-xs, snowflake-arctic-embed-s, bge-small-en-v1.5 — on mini-BEIR + the in-repo hard tier + LongMemEval-relevant retrieval recall (R@10), with the nomic-embed-text-v1.5 / Qwen3-Embedding-0.6B / NVIDIA-Llama-3.2 class documented as the dimension-coordination upgrade path (NOT in this issue's benchmark: they collide with the #265 encrypted tier's 384-dim pinned client embeddings and the 2GB VM feasibility budget); (3) answer the hosted-vs-local embedding UX question as a bounded research deliverable (industry precedent: mem0/Zep/LangMem all embed server-side; local embedding is the self-hosted story) with the final product call routed to the user; (4) implement the winning swap (embeddings.py model, Dockerfile.hosted re-bake + entrypoint + pre-warm, force-re-embed tool, single Point.embedding index rebuild, threshold recalibration) ONLY IF it beats the real MiniLM baseline by a pre-registered delta on the pre-declared primary metric and E2E-8 stays ≤300ms p95; otherwise close with a documented no-swap/keep-MiniLM verdict and re-file the non-embedder levers to #317.**

## Pre-registered Decision Rule (P1 fix from problem-verify)

- **Primary metric:** retrieval recall R@10 on the LongMemEval-relevant category set (temporal + multi-session + information-extraction questions, sampled per the eval's existing oracle/authored query structure). Rationale: the trigger is LongMemEval; the bound is recall coverage, not top-10 ordering; nDCG@10/P@5 reported as secondary.
- **Minimum meaningful delta:** candidate must beat real-MiniLM control by **≥ +5% relative R@10** on the primary metric, with **non-overlapping or paired 90% bootstrap CIs** (the paired-CI machinery already exists in tests/eval/retrieval/bootstrap.py). +5% is pre-registered per the eval README's power math ("detect ≥5% P@10 delta").
- **Multiple-comparison handling:** 4 candidates vs 1 control = 4 pairwise comparisons. Control the FDR at 10% (Benjamini-Hochberg across the 4 pairwise tests). A candidate wins only if it clears the +5% threshold AND survives FDR.
- **Secondary gates:** E2E-8 latency verdict ≤300ms p95 (achieved band, benchmarks/run_report.py — the `benchmarks` E2E-8, disambiguated from the tests/test_backfill_sources.py E2E-8 contract); hosted feasibility (candidate loads + pre-warms on the 2GB VM class within the existing 30s-300s cold-start window, image size documented).
- **Insufficient-power outcome:** if the real-model baseline leaves no headroom to distinguish candidates (P@5 ≈ 0.946 ceiling risk on the synthetic oracle) or CIs straddle the +5% line → **keep MiniLM, verdict: "needs real data"** — closes cleanly, no swap.
- **No-winner outcome:** if no candidate beats the control ≥Δ with FDR-clean CIs on the primary metric → **no swap + re-file to #317** (non-embedder levers: key-expansion, time-aware query expansion, reranking, fusion-fix) with the negative evidence attached. "No swap" and "embedder-bound → re-scope" are distinct outcomes; both named.
- **Ingest-encode note:** per-encode latency is measured and reported on both the query path and the add path (compute_embedding, sdk.py:1418) but is NOT a hard gate — E2E-8 is the gate; corpus re-embed is a one-time batch cost.

## Why This Framing (evidence)

1. **The swap lever is real but bounded** — MemDelta (arXiv:2606.29914): pure embedding swap = +6.2pp LongMemEval-S (p=0.004), largest on temporal (+10.5pp) and multi-session (+11.3pp) — the issue's target categories. ⚠️ single-source preprint. Tortoise stores verbatim turns → write-side gap structurally small → retrieval-side dominant.
2. **The mechanism is misattributed** — arctic-xs (22M, fine-tuned FROM MiniLM) scores 50.15 MTEB-R vs MiniLM 41.95 = **+8.2 at identical size** — the gain is training data (hard-negative mining), not parameters. An open candidate pool is required, not a blind bge-small swap.
3. **The gates are unmeasurable as written** — verified: `baseline-embedded-2026-08-17.json` provenance = `synthetic_query_vectors: True`, `indexes.vector: False` (brute-force, redislite). The vector arm 0.8546 nDCG@10 is model-independent near-ceiling. Docker mode with real model is runnable (run.py:210-226 auto-uses EmbeddingModel; projection recomputes on write) — real baseline is step 0.
4. **Hosted-vs-local resolves as a constraint, not a blocker** — mem0 hosted = server-side baked ("We serve the model ourselves"); Zep removed its bundled local embedding service in CE; LangMem/Letta server-side. Tortoise's current bake already matches the industry hosted answer and is MORE local than mem0's default (no external embedding API call). **Tier-partitioned:** #265 (CLOSED) already mandates client-side embeddings for `encryptionVersion>=1` teams (model+dim+version pinned to MiniLM/384, server never recomputes). The open question is only the DEFAULT tier → user product call at Phase 8.
5. **Cross-issue constraint (#265 encrypted tier)** — shared `Point.embedding` index is dimension-fixed. Encrypted teams ship client-computed 384-dim MiniLM vectors. A server default-model swap must stay 384-dim (all four benchmark candidates do) OR coordinate an index partition / dimension change cross-epic. **This issue stays 384-dim**; the 768/1024 upgrade is filed out.
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
2. Candidate benchmark: MiniLM (control) + arctic-xs + arctic-s + bge-small-en-v1.5 on mini-BEIR (MS MARCO + 3-4 datasets) + in-repo hard tier + LongMemEval-relevant R@10 — extending tests/eval/retrieval/run.py. LongMemEval gate = retrieval recall (gold evidence in top-k), NOT the expensive end-to-end judged runner (that's post-swap for the winner only).
3. Hosted-vs-local research deliverable: mem0/Zep/LangMem/Letta precedent + tier partition (#265) + recommendation; final product call routed to user.
4. Pre-registered decision rule (above): primary metric R@10, +5% delta, FDR 10%, paired 90% CIs, E2E-8 ≤300ms, feasibility gate.
5. Conditional swap implementation (only if gates clear): embeddings.py model swap (384-dim), Dockerfile.hosted re-bake + entrypoint.sh cache path + hosted_api.py pre-warm, force-re-embed flag on backfill_embeddings.py (currently NULL-only), single Point.embedding index drop+recreate, threshold recalibration (0.75/0.40 are model-specific — re-score tortoise's own labeled pairs under the winner; sweep protocol per research), per-encode + E2E-8 latency measurement, decision record (model choice + location answer + threshold values).
6. Supersession block + this doc as the authoritative scoping record.

### OUT (filed as separate issues)
- **Hosted-vs-local product decision** (default-tier embedding location) → user decision at Phase 8; if "customer-local for hosted" wins, project-level issue (mixed vector spaces, model-version pin, SDK work; coordinate with #265).
- **LongMemEval non-embedder levers** (key-expansion, time-aware query expansion, reranking, fusion-fix — fused 0.835 < vector 0.855) → re-file to #317 with negative evidence if no-winner; otherwise independent retrieval-optimization issues.
- **768/1024-dim upgrade** (nomic-v1.5, Qwen3-Embedding-0.6B, NVIDIA Llama-3.2 fine-tunes) → separate issue requiring #265 encrypted-tier dimension coordination + hosted VM feasibility (Qwen3-0.6B ≈ 1.2GB vs 90MB baked; 2GB VM cold-start #545).
- **Full-BEIR/MTEB validation** of the eventual winner → out unless mini-BEIR flags contamination risk.
- **TF-IDF hard-tier lexical advantage** (0.599 > vector 0.530 — hypothesis-generating only, needs real-model confirmation) → separate lexical+semantic hybrid research.

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
| #265 encrypted tier (client embeddings, 384 pin, shared index) | Constraint | OUT → cross-issue note | ✅ |
| LongMemEval data (wipe + re-run) | Data | ✅ (user-confirmed wipeable) | ✅ |
| Self-hosted installs (Dockerfile.selfhost, first-use download 90MB→127MB) | Stakeholder | IN (note: chosen default propagates; MiniLM documented fallback) | ✅ |
| CI (no model — TF-IDF fallback; swap invisible) | Regression surface | IN (threshold/dim regression covered by manual Docker eval) | ✅ |

## Review Cycle Log

### problem-verify — Cycle 1
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
| Overall | standard (Level: task — single atomic deliverable: evidence-gated selection + conditional swap) |
| Library-deps | high (new model dep, version pins, sentence-transformers compat) |
| Architecture | medium (single index rebuild, thresholds, Docker bake) |
| UX | medium (hosted-vs-local research + product call) |
| Ontology | low (no schema/entity changes; embedding property unchanged) |

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
