<!-- research-path: docs/epics/2026-08-20-1509-extractor-v3/02-research-brief.md -->

# #1695 — Kinds Classification-Later + Deterministic Chain Enforcement (pack vocabulary out of the S2/S4 prompts)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Remove the pack vocabulary from the S2/S4 extraction prompts (a scalability ceiling as expansion packs grow) by (1) classifying pack kinds in a post-extraction layer (embedding-retrieve-top-5 → LLM-adjudicate the ambiguous tail, batched 25–50/call) and (2) making pack-chain enforcement a deterministic post-extraction graph pass — validated by A′ diagnostic + 200-item 3-arm A/B before integration, per the owner's mandated sequence.

**Team:** epistemic-team · **Epic:** #1509 (extractor-v3) · **Capstone:** #1549 · **Complexity:** standard+ (Architecture high, Ontology medium, UX low)

**Owner sequencing (MANDATED, #1549 2026-08-25):** PILOT BASELINE (DONE — 50/50, accuracy 0.74, `pilot_20260825T222548Z.report.json`) → **THIS improvement** → PILOT RE-VALIDATION → 500-Q V3 baseline. This plan preserves that order: the improvement ships (as compact-with-chains + chain enforcement + measurement foundation even in the loss branch), the A/B decides whether classify-later joins it, and the 500-Q is gated on the re-validation being non-worse.

**Architecture:** Standalone modules `tortoise/kind_index.py`, `tortoise/kind_classifier.py` (hybrid kNN + LLM adjudication, injectable encoder seam), `tortoise/chain_enforcer.py` (deterministic edge rewire), wired into `extract_session_v2` behind a call-time env toggle `TORTOISE_CLASSIFY_LATER` + an injected `kind_classifier=None` seam. Flag-off = byte-identical legacy path. Renders switch to a core-only mode under the flag; chains ship as an independent change first.

---

### Pattern Research

> **Findings date:** 2026-08-26

**Library docs (preflight)** — no new third-party deps. numpy (runtime), sentence-transformers (`[embeddings]` extra, already the production embedder `BAAI/bge-small-en-v1.5` via `tortoise/embeddings.py`), scikit-learn (TF-IDF fallback in the same extra), requests (pre-existing at code level only — declared in dev group; noted, not promoted in this issue). The classifier reuses the `EmbeddingModel` singleton (never re-instantiated) and the existing model-adapter router (deepseek-direct primary / OpenRouter fallback). No context7 lookup needed — no new library.

**Canonical patterns** — Extract-then-classify is the state of practice for structured extraction at scale: PURE (Zhong & Chen, NAACL 2021) pipelined entity→relation beat all prior joint models by **+1.7–2.8 F1** (ACE04/05, SciERC); MuSEE (arXiv 2402.04437) decomposes into identify-untyped → determine-types → predict-properties and reports staged decomposition *improves* accuracy while cutting output tokens; DEE (arXiv 2406.01045) decomposed event extraction gained **+8.3/+4.6 F1**; GCIE (Findings EMNLP 2024) uses an LLM type-recognizer over a candidate list, then small extractors. The pitfall to engineer against is **error propagation** (a mis-extracted bit is garbage regardless of classifier quality) — the same failure mode already accepted between S1→S2.

**Competitor-variance / many-label classification** — LLMs degrade as the label set grows: LongICLBench (arXiv 2404.02060) near-zero at 174 labels with recency bias; HierLabelNet (ISPRS IJGI 2025) full-space 39.8% micro-F1 → top-5-filtered 62.8%; Label-Space Reduction (Inf. Retrieval J. 2026) +7.0–14.2% macro-F1 by iterative candidate reduction. Label-order primacy is pervasive: Primacy Effect of ChatGPT (EMNLP 2023) label shuffling changed predictions on **87.9%** of TACRED instances (⚠️ transfer tagged [unverified] — TACRED 42 labels, ChatGPT-class; our 54-kind deepseek-flash system is unmeasured until the A′ diagnostic runs); Serial Position Effects (Findings ACL 2025), option-order sensitivity (Findings NAACL 2024). **The standard mitigation is label-order randomization + candidate-set reduction, not vocabulary removal alone** (Fantastically Ordered Prompts) — hence label-order randomization ships regardless of A′ outcome, in all render modes.

**Embedding vs LLM classification (flash-class economics)** — Embeddings outperform prompting on multiclass: **+49.5% accuracy, up to 10× cheaper** (arXiv 2504.04277); 94% vs 82% LLM baseline, 152× faster, 15× cheaper (2026 industry eval); SapBERT/UMLS maps 100k–4M biomedical concepts at **0.85+ acc** (⚠️ transfer tagged [unverified] — biomedical entity-linking regime ≠ open-domain epistemic kind assignment; validated by the bit-level eval set). The honest caveat: embeddings are weak on near-miss/context-dependent pairs (tortoise's `nearMisses`-carrying kinds, 18–23/25 pack kinds) — the hybrid retrieve-then-adjudicate tail is the mandated answer, and bge-small-for-kind-classification is a **new application** tagged [unverified] with the eval set + probe as the in-domain instrument.

**Batch adjudication (pitfalls framing)** — Batch ≤100 stays within ~2pp of single-item while saving **>80% token cost** (arXiv 2604.03684); Multi-Instance Processing (ACL 2026) shows collapse beyond ~200–1,000 instances with instance-count (not context length) driving degradation; **smaller models degrade more sharply at batch scale** (arXiv 2605.28268) → keep batches at 25–50 for the deepseek-flash class; Multi-problem evaluation (ACL 2025 Insights) — ask for per-item labels in a JSON object, never index-selection; `_parse_json` (extractor_v2.py:3158) parses objects, not arrays → batches are object-wrapped. Batch-size must be validated per model (Utrecht 2025) — calibrated in D0-3.

> **Probe gate note (D0-2):** the build is gated on a kind-separability/tail-rate probe of bge-small against the actual 54-kind vocabulary (pairwise inter-kind similarity, top-5 hit rate on gold bits, adjudication-tail fraction) — pennies, no LLM, mirroring the #1746 Approach-C pre-flight-probe precedent. This converts the research transfers into an in-domain measurement before the build commits.

### Integration Surface Map

| Surface | Test Layer | Expected Verification |
|---------|-----------|----------------------|
| `extract_session_v2` entry (sdk.py:1794, :2311; hosted_api via sdk; ingest_v2.py live :789, shadow :362 per #1744; run_v2_pipeline.py:120) | unit + integration (docker) | `kind_classifier=None` default → byte-identical payloads (canonical_json with fixed session_id); env toggle read at single choke point reaches ALL callers |
| S2/S4 renders (`_render_master` :270 family; `render_s2_prompt` :785/:793; `render_s4_prompt` :1161/:1175) | unit (embedded) | core-only render: no pack_kinds, no CHAINS (both `{chains_text}` slot and master inline block :326-333); user-personal-state/granularity/carve-out present (verbose base); `_select_pack_kinds` fallback inverted to `{}` under flag-on; dead block :344-386 removed |
| OUTPUT_CONTRACT + S2_TMPL (unclassified sentinel; S4 re-emit clause) | unit | sentinel emitted for pack-domain content; S4 re-emits S2 items verbatim incl. classifier kinds (MUST-come-from-list scoped to NEW items); never-mint preserved |
| E4 `merge_embed_lists` (:1228) + post-merge kind-preservation re-stamp | integration (docker) | merge fn untouched (byte-identity); S2 classifier kinds survive S4 re-emission; re-stamp observable (census counter on override); kind-freeze SECTION-AWARE key (entity `_norm(name)`, event/point `_norm(content)`) |
| `_find_existing_entity` / `_resolve_superseded` / `link_before_create` | integration | typed refs guaranteed pre-resolution; post-resolution re-key; no duplicate :Object creation on kind mismatch |
| `execute_embed` minted-gate (:200, :2329-2337) + seen_entities | integration | classifier emits namespaced forms (`master_kind_forms` accepts); candidate restriction per type (entities/events/points); `unclassified` never written to graph (→ best core kind + census) |
| `validate_chains` (:1977) → `chain_enforcer` (operators injection) | unit (embedded) | golden fixtures: rewire-when-intermediate-exists, warn-only-without-intermediate, never-invent, never-drop, distance-threshold unreachable case, order-OK no-op; ships independently |
| Downstream kind consumers (search_engine :693-753, mcp_server, migrate_kinds, projection) | regression | unaffected — kinds stay valid (no stored-node rewrites; in-process only) |
| `EmbeddingModel` (embeddings.py) | unit | lazy import (no torch at module level); `get()` None → family fallback; degraded TF-IDF fallback |
| `_complete_parsed` (:804) adjudication | unit | object-wrapped batches (25–50); retry=1 + census; `max_tokens` cap (M3); DeepSeekDirect no-response_format tolerated by prompt-contract parsing |
| Harness `tools/longmem_eval` (run.py fingerprint/run_key; ingest_v2) | integration | `--classify-later` arm; fingerprint/run_key includes the flag; isolated DBs per arm; no checkpoint reuse; resume-quality gate |
| `data/kind_index/` | unit | content-addressed npz (pack-manifest-hash + core-version + embedder id); recompute on pack install; `.gitignore` |
| CI manifests (config/ci-surfaces.yml, test_markers.py, conftest :106-112) | infra | 4 new test files registered; lane identity per file; timeout markers; carve-out pin untouched (prefer lane-agnostic) |

### Pre-Registered Experiment Gates (decision rules — fixed before any run)

**D0-1 · A′ label-order diagnostic** (kind-list-only shuffle hook `TORTOISE_LABEL_ORDER=shuffle`, deterministic seed; ~50 sessions; PAIRED fresh canonical re-run — never crash-era checkpoints; S2-rerun-only, arms share S1/S3/S5 artifacts; bit-level agreement metric):
- **Bias confirmed** (per-bit kind agreement < 95% OR distribution shift ≥ 5pp — the expected outcome per the 87.9% prior): primacy justification validated → A/B win criterion is **accuracy-directional** (flag-on ≥ control within CI) with cost as cap/report.
- **Agreement ≥ 95% + shift < 5pp**: primacy justification dropped (NOT the direction) → A/B win criterion is **cost/parity against the accuracy-floor** (kind floor holds AND cost ≤ 1.1× compact). The classify-later direction survives on cost + label-space-scaling grounds.
- Owner may override either branch (rationale recorded). Label-order **randomization ships regardless**, in all render modes (per-call seeded shuffle — cheap insurance + removes the A/B's own in-arm order confound).

**D0-2 · Kind-separability/tail-rate probe** (embed 54-kind vocab with bge-small; pairwise inter-kind similarity, top-5 hit rate on gold bits, adjudication-tail fraction; no LLM): **BUILD GATE** — tail fraction < 40% AND top-5 hit ≥ 0.85. Failure path: refine kind embeddings (description/synonyms/examples re-weight) and re-probe; if still failing after one refinement, abort the classify-later build and ship compact-with-chains + chains + measurement foundation (documented owner decision).

**D0-3 · Bit-level kind eval set** (in-domain; ≥2,000 bits/arm; SPLIT calibrate ~1,000 / holdout ~1,000+; gold = owner + adjudicator, conflict rule, 10% agreement check, ABSOLUTE labels — not agreement-with-current-system; silver bootstrap + owner audit; nearMiss subset tie-breaker-only; slots included; sentinel rate = separate bounded class; two-sided CI + multiple-comparison handling): SIM_FLOOR / MARGIN / λ calibrated on calibrate, verified on holdout, **frozen before the A/B**.

**D0-4 · Clean pilot baseline**: reference = **fresh-only 0.867** (the 0.74 is a two-population crash-resume blend 0.55+0.867 — NEVER diffed); resume-quality gate (reject checkpoints with fts.count=0 / session@20=0); 50-Q re-validation is a **safety/sanity gate, not the decision gate** — the decision rests on the bit-level eval + A/B.

**A/B (200-item 3-arm; verbose / compact-with-chains / flag-on; optional 4th arm compact+label-randomization) — accuracy-primary composite (PASS requires ALL):**
1. Kind-floor vs **BOTH** verbose and compact: reject if Δ < −8pt (two-sided CI, family-wise corrected); Δ∈[−8,−3] proceed with warning; ≤3pt no-meaningful-degradation.
2. **Emission-recall gate (BLOCK)**: bit-set overlap vs control ≥ 0.95 per stratum (pack stratum oversampled) — the vocab may anchor emit-vs-drop decisions; this is the gate that sees bits that were never emitted.
3. Retrieval regression: evidence@20 ≥ baseline, session@20 ≥ 0.90 (fresh population).
4. **Parse-census equality (BLOCK)**: computed on the **class intersection** of both arms' censuses (flag-only classes `classify_error`/`embedding_error` governed by their own thresholds); no >2× ratio in any common class between arms.
5. Sentinel rate ≤ 5% of bits (else block); classify-error rate ≤ its own threshold.
6. **Cost ≤ 1.1× compact** (CAP, not a savings requirement); ≥10% savings reported as secondary; the 80% claim's base is explicitly the full-vocab verbose render; final cost read at 500-Q (deferred confirmation, not a separate gate).
- All arms: production direct wire (non-reasoning `deepseek-chat` via `_direct_wire_id` — NOT the literal reasoning id); label order randomized per call; isolated DBs per arm; no checkpoint reuse; arms interleaved round-robin; real backend; fingerprint/run_key includes the flag; pre-registered win threshold (expected direction + CI-based rule, M8 discipline).

**Win path**: A/B win (composite) → integrate classify-later behind flag → **50-Q re-validation** (step-3 semantics — re-run of the pilot set, NOT post-500 step-7; protocol amendment recorded; fresh checkpoint; PAIRED flag-off control arm on the same sessions; expected-direction pre-stated) → **500-Q V3 baseline with flag-on** → **default-on only after 500-Q passes** (or owner waiver recorded). **Loss branch**: compact-with-chains + chain enforcement + measurement foundation ship; re-validation + 500-Q proceed on that config; owner may re-open classify-later with the A/B evidence.

### Tasks

#### Task 1: Chain enforcement — standalone `chain_enforcer.py` (ships FIRST, independent of classify-later)

**Intent:** Make pack-chain semantics deterministic and guaranteed (the prompt's "re-map" becomes enforced) as an independent, fixture-gated change that ships before — and is exercised in every arm of — the A/B.
**Acceptance:** `tortoise/chain_enforcer.py` rewires reverse-chain-order `about_entities` pairs via operators injection ONLY when the nearest valid chain position is unambiguous; warns-and-keeps (never invents, never drops) otherwise; `validate_chains` (:1977) stays as the warn-only backstop; golden fixtures pass; flag-off behavior unchanged.
**Files:**
- Create: `tortoise/chain_enforcer.py`
- Modify: `tortoise/extractor_v2.py` (call site between resolve_entities and execute_embed, ~:2851-2866 region; result-surface plumbing)
- Test: `tests/test_chain_enforcer.py` (new; migrates `TestChains` scenarios from `tests/test_extractor_v2.py:923-1000` — delete there, update cites; lane-agnostic — verify `validate_chains`/`execute_embed` are pure: confirmed no embeddings/LLM/DB calls)

**Step 1:** Migrate the existing `TestChains` golden scenarios into `tests/test_chain_enforcer.py` (rewire-when-intermediate-exists, warn-without-intermediate, order-OK no-op, never-blocks) — confirm they pass against the current advisory `validate_chains` before any new behavior.
Run: `uv run pytest tests/test_chain_enforcer.py -v` (embedded lane; note the moved tests now also execute in tier-2 embedded PR legs — pure logic, no URI dependency).
**Step 2:** Implement `validate_and_rewire(embed_list, master) -> (embed_list, notes, stats)` — the mutating superset: reuses `_chain_positions` (:1966) and the intermediate-finding logic (:2028-2036) but injects the repair into `operators`/`about_entities` refs; distance-threshold rule (rewire only when the nearest valid position is unambiguous; otherwise warn-and-keep).
**Step 3:** Add the new golden fixtures: no-edge-drop, no-entity-invention, distance-threshold unreachable case, no-pack-stratum no-op, decoupled-gate (ships with compact OFF).
Run: `uv run pytest tests/test_chain_enforcer.py -v` — all pass.
**Step 4:** Wire the call into `extract_session_v2` after `_apply_entity_resolution` (:2851-2853), before `execute_embed` (:2866); keep `execute_embed`'s internal `validate_chains` (:2654) as the warn-only residual reporter.
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_extractor_v2.py::TestChains -v` (post-migration: docker lane green).
**Step 5:** Register `tests/test_chain_enforcer.py` in `config/ci-surfaces.yml` (append to the `sdk` surface or a new `classify:` surface; no carve-out — lane-agnostic). Commit.

#### Task 2: Measurement foundation — eval set + probes + A′ harness support

**Intent:** Build the instruments that gate every decision (D0-1…D0-4) BEFORE any classify-later code.
**Acceptance:** `tools/kind_eval.py` runs the bit-level eval (≥2,000 bits/arm, calibrate/holdout split, nearMiss tie-breaker-only, pack-stratum minimum audited via `tools/longmem_eval/dataset_audit.py`); the A′ shuffle hook exists behind `TORTOISE_LABEL_ORDER=shuffle`; the D0-2 probe script exists; `data/kinds_gold.jsonl` committed with provenance; `data/kind_index/` gitignored.
**Files:**
- Create: `tools/kind_eval.py`, `data/kinds_gold.jsonl` (or `tests/fixtures/` mini-gold), `data/kind_index/.gitkeep` + `.gitignore` entry
- Modify: `tortoise/extractor_v2.py` (`_render_master` shuffle hook — seeded kind-list shuffle in the `_group()` closures; hint blocks user-personal-state/granularity/carve-out EXCLUDED from shuffle)
- Test: `tests/test_kind_eval_set.py`, render/shuffle tests in `tests/test_extractor_v2.py`

**Step 1:** Run `tools/longmem_eval/dataset_audit.py` on the A/B corpus candidates — measure pack-kind bit density per haystack; record whether the pack-stratum minimum (per-pack n for a −8pt/10pp-per-pack detection ~392 bits/pack) is reachable; if not, oversample pack-heavy haystacks (dev/product-strategy sessions) or extend the corpus — else the pack-stratum gate is downgraded to warn (documented in the run record).
**Step 2:** Author the bit-level gold set: sample in-domain sessions (production S1 capture post-#1468; fallback: dev/product-strategy sessions + trigger-bearing synthetic transcripts), owner + adjudicator labeling with conflict rule, 10% agreement check, split calibrate/holdout, nearMiss subset, slot coverage; silver-bootstrap to ≥2,000 bits/arm.
**Step 3:** Implement the A′ shuffle hook + `TORTOISE_LABEL_ORDER=shuffle` (deterministic seed from story + env override); tests: env unset → default order (regression pin), env set → deterministic seeded shuffle, classification-output invariance under both orders.
**Step 4:** Implement `tools/kind_eval.py` (`--eval <gold.jsonl> --arm <name>`; `--probe` for the D0-2 separability probe) + `tests/test_kind_eval_set.py` (metadata checks: provenance, split, nearMiss exclusion, sentinel bounded class, pack-stratum).
**Step 5:** Run the D0-2 probe; record tail fraction + top-5 hit rate; gate decision (Task 5's build proceeds only if tail < 40% AND hit ≥ 0.85). Commit.

#### Task 3: Kind index — `kind_index.py` + `compile_kind_index_spec()`

**Intent:** The content-addressed, persisted kind-embedding index (bge-small) covering the FULL candidate set (core §5 objects + subjects + points + events + pack kindDefs with description/synonyms/examples/nearMisses) so the classifier can assign core AND pack kinds.
**Acceptance:** `KindIndex` builds from `compile_kind_index_spec()` (new `value_extractor` accessor reading `PackManifest.kind_defs` :152/:355 — `compile_value_brief` :48-50 drops synonyms/examples, so the accessor is new), persists/loads `data/kind_index/<sha256(manifest-hash+core-version+embedder-id)>.npz`, recomputes on hash change; lazy import of the encoder (no torch at module level); `EmbeddingModel.get() → None` handled by caller.
**Files:**
- Create: `tortoise/kind_index.py`
- Modify: `tortoise/value_extractor.py` (`compile_kind_index_spec`)
- Test: `tests/test_kind_index.py` or fold into `tests/test_kind_classifier.py` (stub-encoder lane)

**Step 1:** Write the failing tests: index build with a stub encoder, persist/load round-trip, cache-key invalidation on manifest-hash change, core-vocabulary inclusion (a `core:Project` bit is classifiable), lazy-import guard.
**Step 2:** Implement `compile_kind_index_spec()` (core §5 + SUBJECTS + POINTS + EVENTS + pack kindDefs full) and `KindIndex` (build/persist/load, hash keyed, load-once memoized like `_MASTER_LIST_CACHE` :157).
**Step 3:** Add `data/kind_index/` to `.gitignore`. Run the embedded-lane tests (stub encoder; no torch). Commit.

#### Task 4: Classifier — `kind_classifier.py` (hybrid, injectable encoder)

**Intent:** The classify-later layer: kNN top-5 over the index → margin gate → nearMiss-aware rerank (tie-breaker-only) → batched LLM adjudication of the low-margin tail (25–50, object-wrapped), with all four error paths fail-open and census-counted.
**Acceptance:** `classify_items(items) -> {assignments, stats, warnings}`; SIM_FLOOR/MARGIN/λ calibrated on D0-3 calibrate split and frozen; LLM tail ON by default; unclassified→best-core-kind terminal + census; candidate restriction per type (entities → object+subject kinds, events → event kinds, points → point kinds+statement); namespaced output forms accepted by `master_kind_forms`; `--eval` CLI; encoder injectable (explicit seam).
**Files:**
- Create: `tortoise/kind_classifier.py`
- Test: `tests/test_kind_classifier.py` (stub-encoder core logic + 2–3 real-model smoke tests)

**Step 1:** Write the stub-encoder core-logic tests (fixed numpy fixture vectors): kNN top-5, margin-gate boundaries, below-floor keep, nearMiss tie-breaker with exact-tie fixtures, family fallback (embedder None), adjudication batch mechanics (object-wrapped; bare-array rejection regression), final-fail census, unclassified terminal, closed-vocab gate vs `master_kind_forms`, A′ shuffle invariance. Determinism pins: `np.random.seed`/`random.seed`, `MockModel` temp 0.0.
**Step 2:** Implement the classifier with the injectable encoder seam (constructor param; default = `EmbeddingModel`); lazy import; degraded TF-IDF fallback; the batch adjudication via `_complete_parsed` (:804) with object-wrapped payloads + `max_tokens` cap (M3).
**Step 3:** Real-model smoke subset (2–3 tests): `pytest.importorskip("sentence_transformers")` INSIDE the test body, `_require_model()` (test_cross_lens.py:446 — parameterize to bge-small dir), `pytestmark = pytest.mark.timeout(600)`. Run: embedded lane (stub tests always run; smoke skips without the model). Commit.

#### Task 5: Classify-later integration — flag, renders, stage order, gates

**Intent:** Wire classify-later into `extract_session_v2` behind `TORTOISE_CLASSIFY_LATER` (call-time toggle at the single choke point) with the verified stage order, core-only renders, and the kind-preservation/freeze machinery.
**Acceptance:** Stage order S1 → S2 → classify(S2) → S3 → S4 → E4+re-stamp → classify(union, kind-missing only) → slot re-key → resolve_entities → post-resolution re-key → chain_enforcer → execute_embed; flag-off byte-identical (canonical_json + fixed session_id); `_render_master_core_only` byte-pinned (golden fixture); `_select_pack_kinds` fallback inverted under flag-on; S4 re-emit clause; unclassified sentinel; all classify failures wired into `error_census` (`classify_error`/`embedding_error` + `_classify_error` mapping).
**Files:**
- Modify: `tortoise/extractor_v2.py` (injection param, toggle, `_apply_classify_later`, `_render_master_core_only`, OUTPUT_CONTRACT sentinel, S2/S4_TMPL clauses, dead-block removal :344-386)
- Test: `tests/test_extractor_v2.py` (new `TestClassifyStage`; extend `TestS4Merge` :1635-1757, `TestE4Orchestrator` :1726-1790)

**Step 1:** Write `TestClassifyStage` failing tests: flag-on pipeline happy path; byte-identical flag-off (fixed `session_id`, `canonical_json` compare, flag-off payload does not grow telemetry fields); kind-preservation re-stamp observable (override census counter); section-aware freeze (freezing `objects:plan` does not freeze `subjects:plan`); embedder-down path; adjudication-fail path; unclassified terminal; S4 re-emit clause (pack-typed S2 item survives S4 verbatim).
**Step 2:** Implement `_render_master_core_only` (verbose base minus pack_kinds minus CHAINS — both `{chains_text}` slots :793/:1175 and the master inline block :326-333; user-personal-state/granularity/carve-out retained from the verbose base); `_select_pack_kinds` fallback inversion under the flag; golden-fixture pin of the render; remove the dead renderer block.
**Step 3:** Implement OUTPUT_CONTRACT `unclassified` sentinel + S2_TMPL emit-untyped instruction + S4_TMPL re-emit clause ("re-emit S2 items verbatim incl. classifier kinds; the MUST-come-from-list rule applies to NEW items only; do not re-type S2 items — the classifier owns kind assignment").
**Step 4:** Implement the stage order + `_apply_classify_later` + post-merge kind-preservation re-stamp + slot re-key + post-resolution re-key + census wiring.
Run: docker lane `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_extractor_v2.py -v`; embedded lane for render tests. Commit.

#### Task 6: Harness arm + A/B runner

**Intent:** Make the 3-arm A/B (verbose / compact-with-chains / flag-on; optional 4th arm compact+randomization) executable in `tools/longmem_eval` with the anti-confound discipline (isolated DBs, no checkpoint reuse, interleaved arms, flag in fingerprint/run_key, production direct wire, label-order randomization in all arms).
**Acceptance:** `--classify-later` arm; fingerprint/run_key extended; per-arm report block (cost census, parse census, kind metrics, emission-recall, sentinel rate, flip lists); all gates pre-registered and computed.
**Files:**
- Modify: `tools/longmem_eval/run.py`, `tools/longmem_eval/ingest_v2.py` (live :789; :362 shadow noted dead per #1744)
- Test: `tests/longmem_eval/test_classify_later_arm.py` (lane-agnostic, `eval:` surface, precedent `test_vector_arm.py`)

**Step 1:** Thread the `kind_classifier` instance + flag through `ingest_v2.py` live path (:789) and `run.py`; extend the checkpoint fingerprint/run_key; add the per-arm config to the report block.
**Step 2:** Arm construction tests (lane-agnostic): arm recorded; fresh-checkpoint isolation; flag-off arm byte-identical baseline; label-order randomization invariance.
**Step 3:** Register `tests/longmem_eval/test_classify_later_arm.py` in `config/ci-surfaces.yml` under `eval:` (manual edit — `ci_selection.py --register` cannot target eval, :560-562). Commit.

#### Task 7: Experiment execution + gates (run protocol, not code)

**Intent:** Execute the pre-registered sequence with the documented gates and owner sign-offs.
**Acceptance:** D0-1 A′ + D0-2 probe + D0-3 eval set + D0-4 clean baseline all recorded; A/B run completes with the accuracy-primary composite evaluated; win/loss branch followed; re-validation + 500-Q executed per the win path.
**Steps (run-level):**
1. Owner sign-offs (see Coordination): gate-semantics amendment (comparison point = A/B control on fresh checkpoint, not the step-1 pilot number), A′ outcome→action mapping, step-3-vs-step-7 protocol amendment, win criterion, funding top-up (~$30–38; full-sequence DS balance target ~$55–60), wall-clock budget (post-reboot ~12× pace; measured 29 min/q fallback stated).
2. Run D0-1 (A′), D0-2 (probe), D0-3 (eval set calibration), D0-4 (clean baseline — depends-on #1746 landing).
3. Run the A/B (serialized after #1746; prefer #1745-first per coordination); evaluate the composite; record the decision.
4. Win path: integrate behind flag → 50-Q re-validation (fresh checkpoint, paired flag-off control, direction pre-stated) → 500-Q with flag-on → default-on after 500-Q (or owner waiver). Loss path: compact-with-chains + chains + measurement foundation ship; re-validation + 500-Q proceed.
Run: the exact longmem_eval commands from #1549's resume instruction (production wire, `--workers 1`).

### Back-Compat Story

- **Existing points/entities keep kinds**: the classifier runs only on NEW extractions, in-process on the embed list — no stored-node rewrites, no graph mutations. `core:other` remains a legitimate index kind; `unclassified` is a sentinel that resolves to the best core kind + census at write time (classification is final-at-write; offline re-classification is possible via `tools/kind_eval.py`).
- **Consumers unaffected**: `search_engine.py` kind filters match any kind string; `mcp_server` point/entity creation accepts arbitrary kinds; `migrate_kinds.py` operates on stored data (orthogonal); Layer-1 commit schema + `client_commit_id` unchanged (kinds were already payload fields).
- **Flag-off byte-identity**: `kind_classifier=None` + toggle unset → the only new code on the off-path is the branch check; regression test asserts canonical-json equality with fixed `session_id` and no telemetry-field growth.
- **Chain behavior**: `validate_chains` stays as the warn-only backstop; `chain_enforcer` adds deterministic rewiring with never-invent/never-drop semantics; direct callers of `execute_embed` unchanged.

### Test Strategy (verification plan)

1. **Embedded lane** (`TORTOISE_TEST_CARVE_OUT=1` for the carve-out files; URI-less for pure-logic files): `uv run pytest tests/test_kind_classifier.py tests/test_chain_enforcer.py tests/test_kind_eval_set.py tests/longmem_eval/test_classify_later_arm.py -v` — stub-encoder core logic always runs; real-model smoke skips without the model.
2. **Docker lane**: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_extractor_v2.py -v` — TestChains (post-migration), TestS4Merge, TestE4Orchestrator, TestClassifyStage, render tests.
3. **Byte-identity check**: A′ 50-session set through `extract_session_v2` flag-off on this branch vs `main` — canonical-json hash-compare.
4. **Offline eval**: `python -m tortoise.kind_classifier --eval data/kinds_gold.jsonl --arm compact|flag-on` — deterministic precision, adjudication rate, nearMiss demotion, no-pack-stratum row.
5. **CI registration**: all 4 new test files in `config/ci-surfaces.yml` (new `classify:` surface or appended to `sdk`/`eval`); lane identity per file (pure-logic files lane-agnostic — no carve-out, `TEST_NO_REDIRECT_STEMS` pin untouched); `pytest-timeout` markers where real-model; determinism pins (seeds, temp 0.0, fixture provenance); pytest-asyncio strict default (sync `MockModel` tests only).
6. **Experiment gates**: D0-1…D0-4 + A/B composite + win path, per the pre-registered rules above.

### Coordination Notes

- **#1746 (parse-error robustness — OPEN, parallel)**: the A/B and clean-baseline re-validation are **serialized after #1746** lands (both change the S2 prompt surface; the A/B must run on the post-stabilized prompt; parse-census equality between arms is a gate). Do NOT run the A/B against the pre-#1746 prompt.
- **#1745 (retrieval evidence recall — OPEN, parallel)**: prefer **#1745-first** so the #1695 re-validation absorbs the retrieval fix (clean isolation); the 50-Q re-validation is a **single shared run** (pilot set, fresh checkpoint) serving both — #1695's paired flag-off control arm runs on the same sessions; no duplicate baseline run. Both preserve the payload contract; verify no merge-conflict surface on the shared docker-lane test files at merge.
- **Owner sign-off items** (recorded in the run record / scoping comment): (1) gate-semantics amendment — the pre-500 comparison point is the A/B control arm on a fresh checkpoint, NOT the step-1 pilot number (contaminated 0.74; fresh-only reference 0.867); (2) A′ outcome→action mapping (bias-confirmed / agreement-high branches); (3) step-3-vs-step-7 protocol amendment (the pre-500 re-validation is step-3 semantics — re-run of the pilot set); (4) A/B win criterion (accuracy-directional vs cost-parity conditional on the A′ outcome); (5) funding top-up (~$30–38; full-sequence DS balance ~$55–60) + wall-clock budget; (6) the D0-2 probe build gate and its failure path.
- **Docs filing**: this plan registers under `docs/00_index.md`; the deep-research report (`/tmp/classification-later.md`, 44 sources) is committed to `docs/research/2026-08-26-classification-later.md` in this issue's scope (it currently lives only in /tmp).
