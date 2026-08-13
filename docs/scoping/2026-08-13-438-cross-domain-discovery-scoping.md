---
title: "<!-- issue-scoping: v5.1 double diamond + verify -->"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

<!-- issue-scoping: v5.1 double diamond + verify -->
# Scope — #438: Automated Cross-Domain Connection Discovery (re-scoped 2026-08-13)

> **Issue:** daniel-ospina/tortoise#438 · **Tier:** complex · **Level:** project · **Team:** organisation-design-team
> **Scoping date:** 2026-08-13 · **Mode:** STREAMLINED (diamond phases inline, verify gates via fresh-context `task` sub-agents)

---

## Confirmed Problem

> Tortoise has **no production path that converts cross-lens candidate pairs into VERIFIED IMPL/NAND operators with MEASURED precision at BOUNDED per-cycle cost** — across both (a) multi-source ingest and (b) **separately ingested** streams — while preserving (1) the candidates-never-write/never-decide-semantics contract (`cross_lens.py`), (2) the draft/live EP-safe lifecycle (mining W-3/W-4, #785), and (3) the incremental batch-local cost property (cost ∝ new data, NOT O(n²) over total graph size).

**Why this is the problem (evidence):**
- Candidate generation LANDED (#650): `find_cross_lens_matches` produces similarity-gated candidates above the calibrated 0.40 threshold. The operator-deciders wired today are (a) the deterministic **cue-word gate** (`_cue_gate_pairs`, extractor.py:177 — fires only on similarity-gated *candidates*) and (b) the **LLM relation stage** (`_RelationStage` invoked from `LLMExtractor.run` conversation mode, extractor.py:927-940 via `ConversationMiner._make_extractor` mining.py:230-233 — explicit-assertion-only, whole-transcript, **never receives candidates**, precision unmeasured). **Neither can produce an operator for the motivating cross-vocab pair**: it scores 0.29 — BELOW the 0.40 candidate threshold, so it never becomes a candidate (`find_cross_lens_matches` returns [] for it; tests/test_cross_lens.py asserts candidate-absent non-event), and even if surfaced it carries no cue words. The blocker is the similarity floor + the absence of a candidate-consuming verified-relation mechanism, NOT the cue-gate alone.
- **The LLM relation verifier is the documented #6306 remainder** (docs/plans/2026-08-08-399-embedding-matching.md: D5, D6, `### #6306 Integration Point` §702-719; `_last_candidates` recorded on MockExtractor as the documented integration point). The nearest existing machinery is `_RelationStage` itself — the verifier is a candidate-consuming mode/extension of that stage, not a new abstraction (Phase 3 note).
- **Cross-stream discovery is NOT landed**: `mining.py` has no `multi_source=True` threading and no relation-stage invocation (verified by grep); #784/#1071 landed *content* dedup (alreadyDecided), not *relation* discovery; #785 landed EP-safe promote. The re-scoped target "IMPL/NAND edges between separately ingested research streams" is unmet.
- **Precision is never measured anywhere** — neither the cue-gate nor the LLM relation stage has any precision telemetry; the re-scoped target "verifier precision measured vs manual wiring" has no existing instrumentation to satisfy it.
- **Edge case (live endpoints):** separately ingested streams are independently promotable (promote_point, #785), so cross-stream candidates routinely pair a draft endpoint with a LIVE endpoint. Mining's W-2/W-4 rule (live prior → review queue, no auto-link) is not inherited by the verifier in this scoping — see Open Decisions #2.

**Falsification:** This definition is wrong if (a) `mining.py` already threads `multi_source=True` through a relation stage with precision telemetry (it does not — verified on both `main` and the `fix/1012-session-shared-embedded-db` integration branch), or (b) the #6306 contract was superseded by an existing candidate-consuming verifier (it was not — `_RelationStage` exists but is explicit-assertion-only and **never receives candidates**, in any mode), or (c) an O(n²)-free cross-stream candidate path exists (it does not — `find_cross_lens_matches` is batch-local only).

**Confidence: 82/100** (root causes verified by codebase read; targets from re-scoped issue body; residual uncertainty is the human close-vs-implement decision, see Clarifications).

---

## Original Issue Body (for gate reference)

> **O/I/T (re-scoped 2026-08-13):** Objective: Complete the automated cross-domain connection pipeline — verify candidate edges into operators at scale, and discover connections between separately ingested streams. Indicators: (1) LLM relation verifier turns unverified cross-lens candidates into IMPL/NAND operators with bounded per-cycle cost. (2) Cross-stream discovery finds IMPL/NAND edges between separately ingested research streams. (3) Discovery cost proportional to new data, not total graph size (preserve incremental property). Targets: 2+ research streams connected with verified edges; verifier precision measured vs manual wiring; per-cycle cost bounded and documented. Components: tortoise/cross_lens.py, tortoise/extractor.py, tortoise/mining.py. Remaining work: (1) LLM relation verifier (eldato #6306 contract); (2) cross-stream discovery between SEPARATELY ingested streams (mining Phase-2 dedup #784); (3) explicit decision recorded 08-13: if #399 + mining is considered the realization of this goal, close instead of implementing verifier. Research context: UX modes interactive vs automated; computational budgets push/pull/cron, must NOT scale O(n²); failure modes hallucinated relations; precision/recall vs manual must be measured.

---

## Phase 1+2 — Problem Diamond (INLINE, per STREAMLINED mode)

### Problem-Diverge: Alternative Framings

| # | Framing | Strength | Weakness |
|---|---------|----------|----------|
| A | **Build the LLM relation verifier** (original) — a component that turns unverified cross-lens candidates into IMPL/NAND operators at scale, plus cross-stream discovery. | Matches issue text and #6306 contract; clear deliverable. | Omits the *measurement* and *cost-bound* dimensions; treats cross-stream as if unsolved when #784 landed content-dedup only; risks "build a service" convenience bias. |
| B | **Close the capability gap** — Tortoise lacks a *verified-edge production path*: candidates exist, cue-gate writes only cue-word operators (low recall), and there is no mechanism to (a) decide relations on non-cued similar pairs, (b) do so across separately ingested streams, (c) measure precision of auto-wired edges vs manual. | Targets root causes; measurement is a first-class deliverable; cost bounds explicit. | Larger surface; measurement needs an eval set (effort). |
| C | **Root-cause framing** — of the four root causes of "0 cross-lens relations" (lens dropped — fixed; shared-words gate — fixed; explicit-assertion-only prompt — UNFIXED; no cross-batch candidate generation — UNFIXED), the remaining work is exactly (c) the verifier + (d) the cross-stream fold. | Precise, falsifiable, maps 1:1 to code. | Reads as justification rather than a distinct alternative. |
| D | **Close the issue** — #399 + mining IS the realization; close instead of implementing the verifier (the recorded 08-13 decision). | Cheapest; honors recorded decision. | **Refuted by evidence:** re-scoped targets unmet — mining has no `multi_source`; no relation stage in document mode; precision never measured; separately-ingested streams never co-occur in a candidate-generation batch. Only valid if the human *redefines* the goal to "ingest-time multi-source cue-gate operators". |

**Adversarial / disconfirmation queries (≥3, run against codebase + external):**

- **AQ1 — Does the LLM verifier violate the "candidates never write to the graph and never decide operator semantics" contract?**
  → **No.** The contract binds *candidate generation* (`cross_lens.py` "This module NEVER writes to the graph and never decides operator semantics — it produces candidates **for a verifier**"). The verifier (cue-gate today, LLM in #6306) is the documented *decider* (399 plan D5/D6: "#6306 upgrades the verifier to the LLM without changing candidate generation"). EP-inflation mitigation (corrected): draft-status operators ARE excluded from EP factors today — `EventAPI.add_operator` writes `status:"draft"` (api.py:57-66) and `extract_svbp_factors(include_draft=False)` excludes draft operators AND draft inputs at all four call sites (projection/__init__.py:1059-1083, #780 shared live-only filter). Residual risks: promotion gating (integration-branch `promote_point`) and draft↔live pairs (OD#2) — Slice 1(a) is confirm-and-route, not introduction.
- **AQ2 — Can cross-stream discovery be solved by existing ingest-time machinery WITHOUT a new verifier?**
  → **Partially, and not enough to satisfy the targets.** A multi-document fold (batched `mine_corpus` with per-document lens keys) reuses `find_cross_lens_matches` for candidate generation — that half needs no new machinery. But operator semantics for cross-vocab candidate pairs still require a verifier; the cue-gate only fires on cue-word pairs, and `_RelationStage` only sees within-transcript explicit assertions. ⚠️ **Disconfirmation check (per reviewer):** even with a verifier, the motivating #399 pair (0.291 < 0.40) never reaches it — the candidate floor itself excludes it. Whether the verifier should evaluate sub-threshold pairs (or the floor should be lowered for cross-stream) is an **open decision (#1)**, not an assumption.
- **AQ3 — What precision bar justifies auto-writing operators?**
  → External evidence: two-stage candidate→verify pipelines with ontology/evidence-based verification beat single-stage LLM extraction (KGLLM LREC-2026 framework: canonical matching + Hungarian-alignment triple scoring; LLM pipelines outperform fine-tuned baselines on non-synthetic data — "Prompt Me One More Time" TextGraphs-2024). ⚠️ Pitfall: **gold standards are incomplete** — KGLLM manual analysis found 61.5% of model predictions textually valid but absent from the DocRED gold → automatic F1 is a *lower bound*. LLM confidence is **uncalibrated** (judge-pattern literature) → threshold must be empirically calibrated, not taken raw. **Provisional bar (open decision #3):** auto-write only when verifier precision ≥ 0.80 on a small held-out cross-lens eval set (≥ 30 pairs, 2 independent human reviewers + adjudication); below threshold → review queue (pattern exists: `list_dedup_candidates`/`approve_merge`). Without a numeric bar, "verified" is unfalsifiable and "queue everything" becomes the trivial safe answer.
- **AQ4 — Is cross-stream discovery already delivered by mining Phase-2 dedup #784?**
  → **No.** #784/#1071 landed *content* dedup ("we already decided this" → alreadyDecided IMPL, draft-to-draft, Variant A/B/C) — a different relation than cross-stream *claim implication/refutation* between separately ingested streams. Verified: #1071 body scope is content-dedup only; `find_cross_lens_matches` is batch-local; no cross-batch candidate path exists.
- **AQ5 — Is the incremental-cost property at risk?**
  → **No, by construction** — but only if discovery runs per-batch. `find_cross_lens_matches` is O(batch²) on the points dict it receives. Ingest-time per-batch calls and a batched multi-document fold both keep cost ∝ new data. The failure mode would be a pull/query-time full-graph pass (O(total²)) — explicitly rejected (see solution diamond, Approach 2).

### Assumptions

| Assumption | Status | Evidence / Falsification |
|---|---|---|
| Cross-vocab paraphrase band is 0.35–0.51 cosine (all-MiniLM-L6-v2) | [validated] | `cross_lens.py` docstring, measured 2026-08-07; motivating pair 0.29 below default 0.40 by design |
| Candidates never become operators from similarity alone | [validated] | Module contract + D5 decision (#650); enforced in code |
| Unverified IMPL inflates EP belief confidence | [validated] | #650 documented failure mode; mining plan W-4 draft-only rule |
| LLM verifier is the documented next step (#6306 contract) | [validated] | 399 plan D5/D6 + `### #6306 Integration Point` (lines 702-719); `_last_candidates` attribute |
| Mining `multi_source=True` / document-mode relation stage = #6306 remainder | [validated] | `mining.py` grep: no `multi_source`; no `_RelationStage` invocation; 399 plan "Scope" row 6 |
| Precision vs manual wiring is measurable | [validated] | Methodology exists (schema-constrained eval, canonical matching); needs a small human-gold eval set |
| Verifier LLM provider available without new deps | [validated] | `OpenAICompatModel` (DeepSeek/Gemini/local) + `OllamaModel` in tortoise/models.py; `Model.complete(system,user)` protocol |
| Cue-gate precision is acceptable for auto-write | [unverified] | **Never measured** — measurement is a scoping deliverable; cue-gate is recall-poor by design |
| Operator-write idempotency exists between cue-gate and verifier | [unverified] | `api.add_operator` has no pair-level idempotency (only document mode has within-batch `seen_pairs`); cue-gate + verifier both consuming `_last_candidates` → duplicate IMPL edges → double-weight EP influence. Test + dependency required (see Recommended tests / Dependencies) |
| #784/#1071 dedup + #785 promote landed on the implementation branch | [validated — branch-specific] | TRUE on `fix/1012-session-shared-embedded-db` (promote_point sdk.py:1827; W-2 dedup wired); NOT on `main`/pr-994 (sdk.py:1468-1470: "no public promote API … until #785 lands"). Merge dependency on the integration branch must be explicit |
| Cross-stream discovery needs a batch fold, not a vector index | [refuted — CORRECTED] | A vector index over stored points ALREADY EXISTS in-repo: HNSW on `Point.embedding` at projection init (projection/__init__.py:969), embeddings stored for every non-operator point (projection/entities.py:88-106; sdk.py:734-745), `run_vector_query`/`degradation_chain` wired into SDK search (search_engine.py; sdk.py:4482-4528). Cross-stream = fold (fresh corpus) + bounded ANN pull over the existing index (new-vs-old). See solution diamond correction |
| Freemium cost envelope binds verifier cost | [validated] | #426 constraint (base ops < $20/mo, LLM-per-edge not for freemium); re-scoped target "per-cycle cost bounded and documented" |
| Draft/live lifecycle applies to verifier writes | [validated] | Mining plan §4.2: extraction never auto-wires operators to live points; `promote_point` reviewer-gated (#785) |

### Boundary & Stakeholders

- **Out of scope:** EP belief-propagation changes; ontology changes (ONTOLOGY.md §3.1 IMPL/NAND unchanged); MCP surface additions (mining tools still not exposed — separate epic concern); replacement of the cue-gate (verifier *complements* it — cue-gate stays the zero-cost deterministic verifier for cue-word pairs); vector-index infrastructure; #784/#785 machinery (landed on the integration branch — see branch note).
- **Prior boundary context:** the mining epic plan (04-plan.md:125, W-4 carve-out) already carves "#438 remains discovery of connections between EXISTING Points without a conversation trigger" out of the mining epic — consistent with, and cited as prior definitional evidence for, Framing B+C.
- **Affected but unmentioned stakeholders:** EP/belief-propagation consumers (auto-wired false IMPL inflates confidence in both endpoints); mining pipeline operators (review-queue workflow); CLI/MCP callers of `mine_*`; freemium cost owners (#426); #6306 multi-source synthesis users (the original 8-lens meta-framework use case).

### Problem-Converge (decision + rejected)

**Chosen: Framing B + C merged** — close the verified-edge production-path gap, targeting root causes (c) no candidate-consuming verified-relation mechanism and (d) no cross-batch candidate generation, with measurement and cost bounds as first-class deliverables.

**Framings diverged but not chosen (noted for solution diamond):** (e) *deterministic-only improvement* (richer cue signals / ontology-backed gates, no LLM — cheaper, no calibration problem; rejected here as primary because it cannot fix the no-cue cross-vocab case, the #6306 contract explicitly names the LLM verifier, and the re-scoped indicators name it) and (f) *review-queue-only discovery* (no auto-write; human decides every edge — the natural "minimum" position the close-check must argue against; addressed as an approach axis in the solution diamond, not a framing of the problem). The issue body's own research context lists interactive vs automated UX modes — the interactive/human-gated mode is preserved as the below-threshold review-queue path (AQ3) and the reviewer-gated `promote_point` lifecycle.

- **Rejected Framing A** (issue-literal): incomplete — omits measurement/cost-bound targets; would ship a verifier with no precision story.
- **Rejected Framing D** (close): refuted by evidence above. The close decision is NOT a scoping conclusion — it is a **human decision** that must be surfaced with this evidence (see Clarifications / open questions). The plan therefore sequences the close-check as the FIRST slice (a cheap audit gate), so a human can close at minimal cost if they redefine the goal.

**Falsification check:** (already stated in Confirmed Problem). Additional: if a held-out eval shows the cue-gate already achieves acceptable precision AND the motivating use case (8-lens synthesis) is satisfied by ingest-time multi-source cue-gate operators, then the verifier slice can be downgraded to optional. This is a plan-level gate, not a pre-supposition.

---

## Phase 1.5 — External Research (capped; 6 post-dedup queries)

> **Trigger assessment:** Architecture axis = medium+ (verifier service design, cost bounds — no in-repo precedent for LLM verification; `_RelationStage` is explicit-assertion-only extraction, not verification). Ontology = medium (IMPL/NAND + draft/live semantics — partially covered by mining epic plan §4.2, but LLM-verified-edge semantics are novel). Library-deps triggered (verifier introduces LLM provider usage for relation decisions + cost telemetry). UX = low (review queue reuses existing dedup-queue pattern). → **Fires.**

### Axis Research

> **Framing/AQ mapping (per-framing provenance):** Architecture findings → support Framing B cost-bound + AQ5 (PKGC bounded verification); pitfalls → refute naive threshold-only shortcut (armature judge, clinical-NLP). Precision findings → answer AQ3 (KGLLM methodology + gold-incompleteness caveat + annotator-noise). Ontology findings → support AQ1 boundary (small op set preserved) + the 2-op gate is an advantage. Hallucination findings → scope the verifier prompt (AQ1/root-cause (c)). Integration Docs → Wiring items (token-usage GAP, draft-write GAP, review-queue fit).

**Architecture (high) — two-stage candidate→verify is the established pattern; verifier must test the evidence it weighs; defer corrections to a bounded post-extraction pass.**
- canonical: Evaluation-filtering model collaboration — small model generates candidate entity pairs; LLM scrutinizes and assigns relations only for those candidates; filtering step is indispensable for precision (Ding et al., LREC 2024, aclanthology.org/2024.lrec-main.778). Mirrors Tortoise's cross_lens→verifier split.
- canonical: Progressive KG Completion (PKGC) — verifier ψ authenticates a **bounded candidate set (nc) per iteration**; top-k with root-filter batches keeps mining cost feasible (arxiv 2404.09897). Supports bounded per-cycle verification cost.
- canonical: Judge pattern — LLM-as-judge evaluates worker output; escalation judge reads **only the flagged subset** ("do not pass noise to the judge"); ⚠️ **uncalibrated confidence** pitfall: prose reads authoritative at 55% or 95% (armature docs/JUDGE-PATTERN.md).
- pitfalls: **"A filter is selective only when it tests the same evidence the verifier weighs"** — clinical NLP generator-verifier study: learning filters from verifier rejections failed at scale; a fixed ontology filter captured 49,734 violations (arxiv 2607.00870). → The Tortoise verifier must see point contents + lens/source context, NOT just similarity scores; a similarity-threshold "filter" is not a verifier.
- pitfalls: OAK+MEND — ontology-grounded **post-extraction correction** defers LLM corrections to a single pass, cutting token cost 21-41% while raising ontology consistency 63%→97% (arxiv 2605.29168). → Batch the verifier call over candidates per cycle (one pass per cycle, not per-pair) to bound cost.
- cost: DeepSeek V4 Flash = very low-cost option in Jul-2026 pricing comparisons (morphllm.com/llm-api; benchlm.ai/llm-pricing) — matches repo's default model class.

**Ontology (medium) — keep the relation set minimal; verify against ontology constraints after extraction.**
- canonical: "Prompt Me One More Time" — two-step extraction (candidates → refinement) + **ontology-constraint verification** (Wikidata property constraints) filters hallucinated triplets; outperforms fine-tuned extraction on non-synthetic data (TextGraphs 2024, aclanthology.org/2024.textgraphs-1.5.pdf).
- pitfalls: HCRE — LLMs do **not consistently surpass SLMs** on cross-document relation extraction when the predefined relation set is large; prediction-then-verification helps (arxiv 2604.07937). → Tortoise's 2-op (IMPL/NAND) gate keeps the decision space minimal — an advantage to preserve; do NOT expand the op vocabulary in this issue.
- Tortoise analog: IMPL/NAND between two draft points is structurally valid; semantic validity (does A actually support/refute B?) is the verifier's job. No new stored kinds needed.

**Precision measurement (target-mandated) — methodology exists; gold standards are incomplete; F1 is a lower bound.**
- canonical: Schema-constrained KG-extraction evaluation framework — canonical-name/alias matching, Hungarian-algorithm triple alignment (predicted vs gold), per-document micro-averaged P/R/F1, structured error categories (KGLLM workshop, LREC 2026, lrec-conf.org/proceedings/lrec2026/workshops/kgllm/).
- pitfalls: **Gold standards are incomplete** — KGLLM manual analysis: 61.5% of model-predicted triples were textually valid but absent from the DocRED gold (gold projected from Wikidata; manual wiring will similarly under-annotate vs text) → automatic F1 = **lower bound** on actual quality; false positives vs gold may be gold's false negatives.
- ⚠️ emerging (2 sources, different categories): human annotation itself is noisy — KnowledgeNet annotator analysis: 32% of annotator "mistakes" were actually correct (aclanthology.org/D19-1069.pdf); automatic annotator agreement with human ~high (Bronzi et al., ACL 2012). → Build the Tortoise human-gold eval set with 2 independent reviewers + adjudication, not a single annotator.

**Hallucination control (pitfalls) — verification/correction stages reduce hallucinations; prompt must stay evidence-scoped.**
- canonical: KG-integrated verification reduces LLM hallucination (survey: "Can Knowledge Graphs Reduce Hallucinations in LLMs?" arxiv 2311.07914; KG-retrofitting, AAAI 2026 ojs.aaai.org 29770). ⚠️ single-source survey claims — treated as directional, not quantitative.
- Tortoise-specific: the verifier prompt carries the explicit-assertion-only legacy (`_RELATIONS_SYS`, extractor.py:454) BUT scoped to candidates — per 399 plan: "the prompt gains the verified-candidates context". The verifier decides IMPL/NAND **only for candidate pairs**, never invents new pairs.

### Integration Docs (DRAFT — final at solution-converge)

| Dep | Version/Surface | Findings | Status |
|---|---|---|---|
| LLM provider via `OpenAICompatModel` (tortoise/models.py:26) | DeepSeek / Gemini OpenAI-compat endpoint / local; `Model.complete(system, user) -> str` protocol | No new third-party dep needed for the verifier call itself; DeepSeek V4 Flash is low-cost (benchlm/morphllm, Jul-2026). ⚠️ Provider-variant usage shapes: DeepSeek `usage.prompt_tokens/completion_tokens`; Gemini OpenAI-compat `usageMetadata`; OllamaModel has NO usage surface — normalize in the usage wrapper | ✅ no new dep; ⚠️ usage normalization |
| Draft operator write path | `EventAPI.add_operator` (api.py:128) writes `status:"draft"`; `extract_svbp_factors(include_draft=False)` excludes drafts (projection/__init__.py:1059-1083, #780) | EP-inflation mitigation is OPERATIVE today; Slice 1(a) = confirm-and-route; promotion reviewer-gated via `promote_point` (#785, integration branch) | ✅ existing |
| ANN index over stored Points | HNSW `Point.embedding` (projection/__init__.py:969); embeddings stored for non-operator points (projection/entities.py:88-106; sdk.py:734-745); `run_vector_query`/degradation_chain (search_engine.py; sdk.py:4482-4528) | Cross-stream new-vs-old discovery reuses this EXISTING index (O(|new|×k) hosted; O(|new|×corpus) embedded brute-force — documented degradation) | ✅ existing (corrected) |
| Pair-level operator idempotency | graph-level pair-exists query (operator Point connecting src/dst) | Cross-run re-mine safety (mirrors DE2E-N1); within-run `seen_pairs` extension | ⚠️ new (Slice 1c) |
| Token usage / cost telemetry | `OpenAICompatModel.complete` returns only `str` | ⚠️ **GAP:** no token-usage surface on `Model` protocol → per-cycle cost documentation requires adding usage capture (wrapper or protocol extension) | ⚠️ wiring item |
| sentence-transformers all-MiniLM-L6-v2 | existing `EmbeddingModel` singleton + TF-IDF degraded fallback (`tortoise/embeddings.py`) | Candidate generation dependency — already in place | ✅ existing |
| Precision eval harness | pytest + existing tests/ | No new dep; canonical-name normalization + exact-match scoring per KGLLM-style framework | ✅ no new dep |
| Review queue | `list_dedup_candidates`/`approve_merge` pattern (#1071) | Reuse for below-threshold verifier candidates; ⚠️ data-model is content/entity-dedup-specific (`dedup_candidate`/`dedup_method`/`dedup_similarity`/`dedup_target_id`) — relation candidates need a different payload (src/dst/similarity/LLM-verdict/confidence) → property-model extension is a wiring item | ⚠️ wiring item (fit check) |

---

## Open Decisions (for human — surfaced from verify gate, decision-relevant)

1. **Candidate floor vs motivating pair (#399, 0.291 < 0.40):** the LLM verifier at the current 0.40 contract receives ZERO candidates for the motivating pair — it never becomes a candidate. Decision: lower the floor for cross-stream discovery / evaluate sub-threshold pairs, or accept that the motivating pair is out of scope (it is "topically similar, not logically implied" — verification's job is precisely to decide this). Feeds the close-check.
2. **Live endpoints in cross-stream:** separately ingested streams promote independently → cross-stream candidates routinely pair draft+live. Decision: verifier writes obey the W-2/W-4 live-prior rule (draft-only auto-write; live endpoint → review queue)? This bounds the "2+ streams connected with verified edges" auto-write fraction and is required before "automated at scale" is claimed.
3. **Precision bar:** provisional Wilson 95% CI lower bound ≥ 0.80 on ≥ 30-pair eval (observed ~0.94 at n=30 — the human must see this number) OR the warm-up acceptance gate (queue-only for first K cycles, acceptance-rate bar, then flip to auto-write — accepted as primary calibration mechanism per [QWEN-GATE] P2-4). Decision: confirm bar + eval-set sizing (tied to EP-inflation tolerance, freemium cost).
4. **Close-vs-implement (recorded 08-13):** this scoping's evidence shows the re-scoped targets are unmet (no multi_source threading, no relation stage in document mode, no precision telemetry, no cross-batch candidate path — verified on both main and the integration branch). Recommendation: **implement** (close-check as first plan slice); close only if the human redefines the goal to "ingest-time multi-source cue-gate operators" (Framing D).
5. **Freemium-tier gate + numeric cost cap (from solution-verify):** #426 says LLM-per-edge not for freemium — but no slice gates verifier runs by tier and no numeric per-cycle budget is pinned. Provisional: cap ≤ 200 candidates/cycle (token/$$ estimate vs $20/mo envelope); decision: verifier runs paid-only vs capped-freemium vs queue-only on freemium. Related cliff: if OD#1 lowers the floor to admit sub-threshold pairs, the 0.15-0.40 band is far larger than ≥0.40 → candidate-volume explosion the cap must absorb.

## Phase 2.5 — problem-verify GATE

### problem-verify — Cycle 1
- Verifier A: P0=0, P1=0, P2=4, P3=4, P4=1 (P2s: LLM relation stage exists in mining conversation path — evidence overstated; idempotency gap; HITL framing not diverged; research per-framing provenance)
- Verifier B: P0=0, P1=2, P2=3, P3=3, P4=1 (P1-1: 0.40 threshold — not cue-gate — blocks motivating pair; P1-2: live-endpoint tension; P2s: AQ1 draft-write caveat, no numeric precision bar, branch provenance)
- Controller action: Fixed P1-1 (mechanism correction + Open Decision #1), Fixed P1-2 (edge case + Open Decision #2), Fixed P2s (evidence attribution, idempotency test/dep, precision bar OD#3, HITL framings e/f, provenance mapping, branch note, AQ1 caveat, test count).
- Re-dispatching...

### problem-verify — Cycle 2 (re-dispatch)
- Verifier A: P0=0, P1=0, P2=0, P3=0, P4=2 (P4-1 idempotency dep cross-ref dangling; P4-2 W-2/W-4 vs W-3/W-4 reference — cosmetic)
- Verifier B: P0=0, P1=0, P2=0, P3=1, P4=2 (P3-1: OD resolution ordering vs close-check must be pinned in plan — slice 0 ordering; P4-2/P4-3 decimal drift)
- Controller action: Incorporated P3-1 (plan slice 0 = resolve OD#1/OD#3 → close-check → implement), noted P4s. Gate PASSES — clean at P0/P1.
- **Exit: no P0s, no P1s → gate passed (2 cycles).**

## Phase 3 — Codebase Explorer (INLINE)

### Affected files + line refs

| File | Location | Role | Change surface |
|---|---|---|---|
| `tortoise/cross_lens.py` | `find_cross_lens_matches` (whole module, 121 lines) | Recall-only candidate generation; contract: never writes/decides | Mostly unchanged; MAY add `batch fold` helper or accept lens derivation for document folds |
| `tortoise/extractor.py` | `_cue_gate_pairs` ~184-247; `_last_candidates`; `_RELATIONS_SYS` :454; `_RelationStage` :735-757; `LLMExtractor.extract_from_document` :1049 | Cue-gate = deterministic verifier; `_last_candidates` = documented #6306 integration point; relation stage = explicit-assertion-only | Verifier module plugs here; `_RelationStage` gains verified-candidates mode; LLMExtractor document mode gains verifier call |
| `tortoise/mining.py` | `ConversationMiner.mine` :121-146; `mine_conversation` :476; `mine_corpus` :503; `mine_corpus_with_sdk` :536 | No `multi_source`; no relation stage; dedup landed elsewhere | Multi-document fold (lens=source/file), relation-stage invocation, verifier cost telemetry |
| `tortoise/sdk.py` | `_semantic_dedup` :3803; `_content_exists` :3713 (dedup landed) | Dedup machinery — NOT relation discovery | Unchanged; reference for review-queue pattern |
| `tortoise/api.py` | `add_operator` :128 | Operator write path | Verifier writes must respect draft lifecycle (create_operator/promote_source=False — check what api.add_operator does vs mining's draft path) |
| `tortoise/models.py` | `OpenAICompatModel` :26, `OllamaModel` :69 | LLM provider | Add token-usage capture for cost telemetry (if protocol extended) |
| `tortoise/embeddings.py` | `_encode`, `cosine_similarity_matrix` | Shared encoder | Unchanged (read-only consumer of ANN index machinery) |
| `tortoise/search_engine.py` | `run_vector_query` :334 (HNSW + brute-force fallback + circuit breaker); `degradation_chain` :579 | ANN retrieval primitive for Slice 4(b) | **READ-ONLY** consumer (Slice 4(b) pins score recompute + floor policy — no changes to search machinery) |
| `tests/test_cross_lens.py` | 17 tests | Candidate generation | Extend for fold + verifier contracts |
| `tests/test_extractor.py`, `tests/test_mining.py`, `tests/test_de2e1_entity_extraction.py` | — | Pipeline tests | Extend for verifier integration + cross-stream E2E |

### Partial implementations found
- `_RelationStage` (extractor.py:735) — the LLM relation model EXISTS but is explicit-assertion-only, runs per single-document extraction, never receives candidates. **The verifier is a mode/extension of this stage, not a new abstraction.**
- `_last_candidates` (extractor.py:200-215) — recorded but no consumer (documented #6306 integration point).
- `MockExtractor.multi_source` (extractor.py:184-247) — full candidate→cue-gate pipeline exists as the M0 template the verifier replaces.
- `mine_corpus_with_sdk` (mining.py:536) — multi-document gather scaffold exists (security/resume/file_hash); lacks lens-keyed fold + relation stage.
- Review-queue infra (#1071) — `list_dedup_candidates`/`approve_merge` reusable for verifier candidates.

### Recommended tests (to be detailed in plan)
1. Verifier unit (`verify_relations` MockModel mode): candidate pair {support}→IMPL, {refute}→NAND, {none}→no operator, {low-confidence}→review queue; justification + confidence in output; explicit `none` ≠ absence.
2. Verifier never writes outside draft lifecycle; EP never sees draft operators (reuse **DE2E-4** harness `tests/test_ep_draft_filter.py`).
3. **Idempotency (within-run):** cue-gate + verifier both fire on the same candidate pair → exactly ONE operator. **Idempotency (cross-run):** re-run fold/re-mine with previously-folded points → NO duplicate operators (graph-level pair-exists check).
4. **Verifier failure fallback:** LLM parse-error / timeout / model-unavailable → 1 retry + backoff → skip batch + route to queue; per-batch atomicity (no partial writes), audit events.
5. **Empty-candidate zero-cost:** empty candidate cycle → NO `complete()` call, zero tokens (trivially-assertable incremental-cost case).
6. Cross-stream E2E (genuine TWO-cycle ingest): stream A mined; stream B ingested later; new-vs-old verified IMPL/NAND edges found at bounded cost; fresh-fold path covered separately; incremental cost assertions (fold size ≤ cap; no full-graph query in hosted paths; embedded mode-conditional). **Load-bearing assertions:** (a) cross-stream provenance — edges connect A-lens↔B-lens points (not within-B vacuously); (b) path provenance — spy on `run_vector_query`: assert the ANN pull (not the fold) produced the new-vs-old edges (no fold over A's points on cycle 2; per-batch sizes ≤ cap).
7. Precision eval: eval set sampled from production candidate distribution (post-threshold per OD#1); exact-pair + op_type agreement scorer; 2 reviewers + adjudication (κ recorded); P/R/F1 with gold-incompleteness lower-bound caveat; confidence-threshold calibration.
8. Cost telemetry: per-cycle token/$$ budget assertion (numeric cap per OD#5); empty-cycle zero-token (test 5); degraded-mode candidates routed (review queue) or flagged in prompt.

### Dependencies
- `sentence-transformers` (existing), LLM provider (existing), pytest (existing). No new third-party deps identified.
- ⚠️ Hard dependency: **draft-only operator write path** — CONFIRM-AND-ROUTE, not introduction: `EventAPI.add_operator` already writes `status:"draft"` and EP factors exclude drafts (`include_draft=False`); Slice 1(a) verifies verifier writes default to draft and promotion remains reviewer-gated (`promote_point`, #785, on the integration branch).
- ⚠️ Hard dependency: **token-usage surface** for cost telemetry (WIRING item) — normalized across providers (DeepSeek `usage.prompt_tokens/completion_tokens` vs Gemini OpenAI-compat `usageMetadata`; OllamaModel has NO usage surface — wrapper/protocol extension must handle per-provider parsing + cost conversion).
- ⚠️ Hard dependency: **pair-level operator idempotency** — within-run `seen_pairs` extension AND graph-level pair-exists check (cross-run re-mine safety; mirrors DE2E-N1).
- ⚠️ **Branch dependency:** #784/#1071 dedup queue + #785 promote_point live on `fix/1012-session-shared-embedded-db` only, NOT `main` (sdk.py:1468-1470). Implementation must branch from / merge the integration branch — verify before planning test reuse.
- Human dependency: close-vs-implement decision (gate at plan start).

---

*Phases 4-8 appended after solution diamond + gates.*

---

## Phase 4+5 — Solution Diamond (INLINE)

### Solution-Diverge: Distinct Approaches

**Approach 1 — In-process pipeline stage: verifier as `_RelationStage` candidate-verification mode + multi-document fold (the #6306 contract path).**
- *Description:* Extend the existing `_RelationStage` (extractor.py:735) with a candidate-verification mode that consumes `_last_candidates` (or re-runs `find_cross_lens_matches` over a folded batch). One batched LLM call per cycle over candidate pairs; returns `{src, dst, op_type, confidence, direction}`. Writes via the draft operator path. Precision telemetry + per-cycle token budget. Cross-stream: `mine_corpus` multi-document fold (per-document lens key = source/file), reusing the SAME verifier.
- *Architecture:* no new module — a mode + a fold + telemetry. Reuses `Model.complete` protocol, `find_cross_lens_matches`, `_RelationStage`, review-queue infra.
- *Files touched:* tortoise/extractor.py, tortoise/mining.py, tortoise/models.py (token usage), tortoise/sdk.py (review-queue fit), tests.
- *Risks:* prompt-craft risk (verifier must not re-invent pairs — only judge candidates); duplicate-write risk (cue-gate + verifier on same pair → idempotency); candidate floor fixed at 0.40 (OD#1).
- *Tradeoffs:* push-model only (discovery at ingest/fold time; historical points need a re-run); smallest surface; matches documented contract.
- *Best fit if:* the goal is the #6306 contract path with incremental cost and minimal new abstraction.

**Approach 2 — Standalone verifier service/module + pull/cron discovery (CORRECTED after solution-verify).**
- *Description:* NEW `tortoise/verifier.py` with a public `verify_candidates()` API + a scheduled/query-time discovery pass over NEW vs EXISTING stored points (pull or cron budget).
- *Architecture:* decoupled module; discovery pass queries stored Points.
- *Files:* tortoise/verifier.py (new), mcp_server.py / sdk.py, vector-index infra (existing — see correction).
- *Risks:* ⚠️ a full-graph brute-force pull pass = O(new × corpus) (violates the cost target for large corpora; embedded mode has no index) — the existing HNSW index makes this O(new × k) on hosted, but embedded mode degrades; cron ops = new operational surface; duplicates `_RelationStage` machinery. The INDEX is not the blocker — the SERVICE surface and duplicate machinery are.
- *Tradeoffs:* covers historical points without re-mining; query-time latency; highest surface.
- *Best fit if:* users must discover connections over the EXISTING graph on demand (pull), or streams are ingested rarely and discovery is expected at schedule time (cron).

**Approach 3 — Verifier annotates only; operators via human-gated review queue.**
- *Description:* The verifier produces *annotated candidates* ({src, dst, op_type, confidence, justification}); operators written ONLY via reviewer-approved promote (reuse `promote_point`/list-candidates pattern, #785). Zero EP risk. Precision measured initially as human-acceptance rate.
- *Files:* extractor.py (verifier mode), sdk.py (queue data-model extension), mining.py.
- *Risks:* per-cycle HUMAN cost unbounded as volume grows — conflicts with "turns unverified candidates into operators at scale" indicator; dilutes "automated" headline.
- *Tradeoffs:* safest (EP-inflation impossible); slowest to "at scale"; natural fallback if precision bar unattainable.
- *Best fit if:* EP-risk tolerance is low or the precision bar (OD#3) can't be met — the fail-safe variant of Approach 1.

**Approach 4 (axis, folded into 1/2): streaming vs cron.** Ingest-time per-batch = streaming (Approach 1's model, cost ∝ new data). Cron = scheduled re-run of the fold over accumulated documents — a trivial wrapper over Approach 1 (per-cycle budget must include it) or full-graph = Approach 2.

### Solution-Converge (decision + rejected)

**Chosen: Approach 1 primary, with Approach 3's review-queue as the below-threshold/low-confidence output path; Approach 2 rejected as a SERVICE — but its index-based new-vs-old mechanism is INCORPORATED into Approach 1 (hybrid, per solution-verify P1 correction).**

*Rationale (quality over convenience):* Approach 1 is the *better outcome* — it satisfies the re-scoped indicators: (1) verifier turns candidates into operators at scale (batched LLM verification with bounded per-cycle budget), (2) cross-stream discovery, (3) cost ∝ new data. It honors the documented #6306 contract (`_last_candidates` consumer), reuses in-repo machinery (zero new deps), and the fold makes cross-stream use the SAME verified-relation mechanism. ⚠️ **Post-verify correction:** the fold alone CANNOT deliver indicator (2) for separately ingested streams at indicator (3)'s cost — a fold over accumulated corpus is O(corpus²)/cycle, while a fold over new files only yields zero cross-lens candidates (same-lens exclusion). The hybrid adds a bounded new-vs-old pass over the EXISTING ANN index (O(|new| × k) hosted / O(|new| × corpus) embedded): fresh-corpus fold for first-cycle recall + index pull for subsequent streams. This is the honest composition that satisfies BOTH indicators.

**Rejected alternatives (corrected):**
- **Approach 2 as a SERVICE** (standalone module + cron): *when it would have been better:* if the product requires on-demand/query-time discovery over the full existing graph, or ingest-time cost must be zero. Rejected because: the service surface (new module, cron ops, MCP/CLI exposure) duplicates `_RelationStage`; embedded mode's brute-force new-vs-old pass degrades to O(new × corpus). The vector-index infra premise was WRONG (index exists) — corrected; the index-based new-vs-old mechanism is absorbed into Approach 1's Slice 4.
- **Approach 3** as PRIMARY: *when it would have been better:* if EP-risk tolerance were zero or the precision bar unattainable — kept as the below-threshold output path, not the primary, because per-cycle human cost is unbounded and the re-scoped indicator says "at scale".
- **Deterministic-only improvement** (framing e): *when it would have been better:* if freemium cost dominated and cue-gate recall sufficed; cue-gate remains the zero-cost first pass.
- **Batch cron wrapper**: *when it would have been better:* streams ingested rarely with scheduled discovery expectations — optional later slice; the fold+index hybrid covers the primary case.

### Draft Plan

**Slice 0 — Decision gate + close-check (human gate):** resolve Open Decisions #1 (candidate floor / sub-threshold intake), #2 (live-endpoint write policy), #3 (precision bar), #5 (freemium-tier gate + numeric cost cap) → run the close-check audit (does #399 + mining meet the human-*redefined* goal? — BLOCKED pending OD#1, which narrows/expands the goal) → implement-vs-close decision committed to the issue; AC set re-baselined per OD resolutions. [Pinned ordering per problem-verify P3-1 + solution-verify.]

**Slice 1 — Wiring pre-flight (hard deps):** (a) confirm+route verifier operator writes through the draft path (draft-status already EP-excluded — verify verifier writes default to draft; promotion stays reviewer-gated); (b) token-usage capture on `OpenAICompatModel` (protocol extension or wrapper; normalized across providers, OllamaModel wrapper); (c) pair-level operator idempotency — within-run `seen_pairs` extension + graph-level pair-exists check before write; (d) merge/branch off `fix/1012-session-shared-embedded-db` (promote_point + dedup queue live there, not main); (e) pin numeric per-cycle candidate cap (provisional ≤ 200; OD#5) + cost conversion rates + **candidate truncation order when volume exceeds cap: PKGC-style top-similarity-by-recomputed-cosine (default)** — selection bias otherwise leaks into Slice 3 precision measurement and Slice 5 queue.

**Slice 2 — Verifier core:** `_RelationStage` candidate-verification mode. Prompt contract (pinned): input per candidate = {src_content, dst_content, src_lens, dst_lens, similarity, source_context} — the verifier must weigh the SAME evidence it decides on (Phase 1.5 pitfall, arxiv 2607.00870), never pair-ids + similarity alone; explicit-assertion-only SCOPED to candidate pairs — ⚠️ semantics pin (per solution-verify P2): the prompt must be unambiguous that it **judges candidate pairs, weighing contents+context, and MAY conclude implied relations** (paraphrase-band 0.35-0.51 and sub-threshold pairs are candidates whose relation is verification's job — OD#1) — NOT "confirm explicit assertions only", which would re-install root cause #5 (399 plan line 56) and collapse the cross-vocab auto-write fraction by prompt design; output `{relations:[{src,dst,op_type:IMPL|NAND|none,confidence,direction,justification}]}` — justification required (review-queue display + adjudication), `none` explicit (no-relation ≠ absence). One batched call per cycle; per-cycle candidate budget (PKGC-style; provisional cap ≤ 200 candidates/cycle — see OD#5). MockModel gains a `verify_relations` TASK mode (support→IMPL, refute→NAND, none→no op, low-confidence→queue) for deterministic tests. **Failure policy (pinned):** parse error / timeout (OpenAICompatModel 60s) / model unavailable → 1 retry with backoff, then skip batch + log + route candidates to queue (mirrors document-mode `skip_on_failure`); **atomicity: all-or-nothing per batch** with audit events per rejected relation — NOT per-operator try/except (corrects the document-mode mirror contradiction). **Cross-run idempotency:** graph-level pair-exists check (query for an existing operator Point connecting src/dst) before write — re-runs/forced re-mines never duplicate operators. **Never invents pairs — judges candidates only.** ⚠️ Also: wire `find_cross_lens_matches` into the LLMExtractor document/fold path (MockExtractor already does it in multi_source mode — port the pattern) so `_last_candidates` is populated on the production LLM path, not just MockExtractor.

**Slice 3 — Precision measurement:** held-out eval set sampled from the PRODUCTION candidate distribution with ≥ 30 pairs; **reviewer provenance (pinned per [QWEN-GATE] P1-1):** gold reviewers must be INDEPENDENT of the verifier under test — agent reviewers from a DIFFERENT provider than the verifier (verifier defaults to DeepSeek family; correlated gold inflates measured precision) with verifier↔reviewer agreement reported, OR a human-stratified sample (10-15 pairs) with agreement reported, OR the correlation risk explicitly recorded in AC2. **Sampling protocol (pinned at Slice 0 per [QWEN-GATE] P1-2):** at Slice 3 the "production candidate distribution" doesn't exist yet (ANN pull lands in Slice 4) — pin either (a) eval on fold-distribution candidates now + verify the ANN-pull distribution matches post-Slice-4, or (b) adopt the **warm-up acceptance gate** ([QWEN-GATE] P2-4, accepted as the primary calibration mechanism): run the verifier in queue-only mode for the first K cycles (≤200/cycle), measure HUMAN acceptance rate on real production candidates continuously, flip to auto-write when acceptance clears the bar over a sliding window — dissolves the sampling circularity, doubles as the confidence-calibration step (Phase 1.5: LLM confidence uncalibrated), and reuses the already-built queue path; slower ramp accepted. **Gate semantics (pinned per [QWEN-GATE] P2-5):** the bar is the Wilson 95% CI LOWER BOUND ≥ 0.80 (i.e., observed precision ~0.94 at n=30 — the human must see this number before OD#3 commits); NOT the raw point estimate. Exact-pair + op_type agreement scorer (IMPL direction semantics + NAND symmetry; NOT Hungarian triple alignment); P/R/F1 report vs manual wiring gold with gold-incompleteness caveat (measured precision is a LOWER BOUND — false positives vs gold may be gold's false negatives, KGLLM 61.5%).

**Slice 4 — Cross-stream discovery (TWO-part mechanism, corrected):**
(a) **Fresh-corpus fold:** `mine_corpus` multi-document fold — per-document lens key (source/file); `find_cross_lens_matches` over the fold; verifier consumption. Batch-local O(batch²) — fine for first-cycle / fresh corpus. ⚠️ Pin lens assignment at fold time (explicit lens key; source-less points → distinct synthetic lens or log+skip — the `unknown`-collapse bug silently zeroes same-lens-excluded pairs).
(b) **Incremental new-vs-old discovery (the separately-ingested case):** bounded pull over the EXISTING ANN index (`run_vector_query`, search_engine.py:334 — HNSW hosted / brute-force embedded; `tortoise_fts_query`/`degradation_chain` at sdk.py:4482-4528 is the FTS fusion wrapper, NOT the mechanism) — new Points queried against stored Points, k-nearest per new point (O(|new| × k) hosted HNSW; O(|new| × corpus) embedded brute-force — documented degradation, search_engine.py:423-437), same-lens pairs post-filtered (lens resolved from provenance.source_id per retrieved point — SAME explicit-lens pin as 4(a)), per-cycle budget cap (OD#5). **Score/floor pins (per solution-verify P2):** `run_vector_query` returns rank-based pseudo-scores (hosted `1.0 - i/total`) or Euclidean-derived (embedded `1/(1+d)`) — NEVER cosine, and has NO 0.40 gate. The verifier's `similarity` input evidence must be uniform: **recompute cosine from stored 384-dim embeddings on pulled pairs** (batch-local, O(k²·d) per new point — preserves AC4). Floor policy: whether the 0.40 gate applies to pulled candidates (post-filter by recomputed cosine) or top-k admission is floor-less is folded into **OD#1** (note: floor-less top-k NATURALLY admits the sub-threshold motivating pair 0.29 — partially resolving OD#1 without lowering the fold floor — surfaced for the Slice 0 decision). Circuit-breaker-open windows (search_engine.py:363-365) route like the embedded degradation (documented, queue/flag), not silent zero. Draft-only writes + live→queue per OD#2. E2E rewritten as a genuine TWO-CYCLE ingest (stream A mined; stream B ingested later; new-vs-old verified edges found at bounded cost). Full re-fold documented as a separately-budgeted wrapper (never the default path).

**Slice 5 — Review-queue integration:** below-threshold / low-confidence / degraded / live-endpoint / verifier-failure candidates → review queue. Queue spec (corrected vs dedup model): new relation-candidate flags + payload {src, dst, similarity, op_verdict, confidence, justification, POINT CONTENTS + lens/source context for the human to weigh the same evidence}; `candidate_type='relation'` filter in `list_dedup_candidates`; approve → draft operator write with Variant-C live-prior deferral to promotion (#785) — NOT the dedup merge action; reject → audit event. Approval API: extend `approve_merge` vs new method — decide at design.

**Slice 6 — Cost telemetry + documentation:** per-cycle token/$$ budget assertions (bounded + documented in mining plan); provisional numeric cap ≤ 200 candidates/cycle (Slice 1 pins the number; token/$$ estimate vs #426's $20/mo envelope); freemium-tier gate (OD#5); per-cycle cost report committed with each run; empty-candidate cycles asserted ZERO tokens (no `complete()` call — the trivially-assertable incremental-cost case).

**Testing strategy:** unit (MockModel `verify_relations` semantics, idempotency, failure fallback, empty-candidate zero-token) → integration (extractor/mining pipeline with draft lifecycle — EP-draft harness is **DE2E-4** (`tests/test_ep_draft_filter.py`), not DE2E-8) → E2E (TWO-cycle ingest: 2 separately ingested streams → verified edges at bounded cost; incremental cost assertions) → precision eval (Slice 3) → cost budget assertions (Slice 6).

**Acceptance criteria (verifiable):**
- AC1: 2+ separately ingested research streams connected with verified IMPL/NAND edges (E2E = genuine two-cycle ingest; **fixture includes a sub-threshold/floor-less pair per OD#1 resolution so the acceptance criterion tests the problem's own motivating 0.29 case** — [QWEN-GATE] P2-3).
- AC2: Verifier precision measured vs manual wiring on eval set sampled from the production candidate distribution (P/R/F1 report; measured precision is a LOWER BOUND — gold may under-annotate; auto-write gate at OD#3 bar via CI-lower-bound, Slice 3).
- AC3: Per-cycle cost bounded and documented (numeric cap per OD#5, token/$$ budget assertion, Slice 6).
- AC4: Discovery cost bounded and proportional to new data — fresh fold O(batch²); incremental new-vs-old O(|new| × k) via existing index with batch-local cosine recompute; NO full-graph brute-force pass in hosted paths; **assertion mode-conditional** (`is_embedded` → assert bounded per-cycle scan count instead of "no full-graph query" — the brute-force fallback IS a per-new-point full scan by design, search_engine.py:423-437); the ≤200 cap governs CANDIDATES, not scans (retrieval scans are bounded by k and |new|).
- AC5: Candidates-never-write/never-decide contract preserved (`cross_lens.py` semantics untouched; verifier is the decider).
- AC6: Draft/live EP-safe lifecycle preserved — draft-status operators already excluded from EP factors (`extract_svbp_factors(include_draft=False)`, #780 shared live-only filter); verifier writes default to draft; live-endpoint behavior per OD#2 resolution; promotion stays reviewer-gated (#785). [AC set re-baselined once OD#1/#2/#3/#5 committed — Slice 0]
- AC7: Close-vs-implement decision + OD resolutions documented on the issue (Slice 0 output).

**Runtime prerequisites:** LLM provider key via `OpenAICompatModel` (DeepSeek/Gemini/local — no new dep); sentence-transformers embedding model (existing); merge target `fix/1012-session-shared-embedded-db`.

**Plan-level gates:** Slice 0 = human gate. Slice 3 = precision-bar gate (auto-write only above OD#3 bar). Slice 6 = cost-budget gate (per-cycle cost documented).

## Phase 5.5 — solution-verify GATE

### solution-verify — Cycle 1
- Verifier A: P0=0, P1=1, P2=1, P3=3, P4=3 (P1: Slice 4 fold has no old-stream-points mechanism within the cost bound — fold over accumulated corpus = O(corpus²); fold over new files only = same-lens exclusion → zero candidates; P2: cross-cycle idempotency once fold includes previously-folded points; P3s: gold-incompleteness not surfaced at Slice 3 gate, degraded-candidates unrouted, queue payload lacks point contents, verifier failure fallback unpinned, MockExtractor-only `_last_candidates`)
- Verifier B: P0=0, P1=2, P2=4, P3=5, P4=1 (P1-1 [borders P0]: fold's incremental-cost claim false for separately-ingested case — indicators 2 & 3 mutually exclusive under A1 as specified; P1-2: **vector index ALREADY EXISTS in-repo** (HNSW Point.embedding, projection/__init__.py:969, sdk.py:4482-4528) — A2's rejection premise factually false; hybrid fold+ANN-pull is the better outcome; P1-3: failure policy + cross-run idempotency unpinned; P2s: eval-set sampling distribution, freemium gate/cost cap, review-queue under-specified, prompt input evidence; P3s: EP-draft exclusion IS operative today (AQ1 overstated), DE2E-4 not DE2E-8, scorer instrument mismatch, Integration Docs rows missing, lens unknown-collapse, token-usage provider variance)
- Controller action: Fixed P1-1 (Slice 4 rewritten as TWO-part mechanism: fresh-corpus fold + incremental new-vs-old via existing ANN index), Fixed P1-2 (corrected Assumptions row + A2 rejection + hybrid incorporated into Approach 1), Fixed P1-3 (failure policy pinned: 1 retry + backoff → skip+queue; per-batch atomicity; graph-level pair-exists cross-run idempotency), Fixed P2s (eval sampling from production distribution + CI-lower-bound; OD#5 freemium gate + ≤200 cap; Slice 5 queue spec with contents + Variant-C deferral; prompt contract with evidence input + justification), Fixed P3s (AQ1 corrected — EP-draft exclusion operative; DE2E-4 harness; exact-pair scorer; Integration Docs rows added; lens-assignment pin; token-usage provider normalization). Re-dispatching...

### solution-verify — Cycle 2 (re-dispatch)
- Verifier A: P0=0, P1=0, P2=3, P3=3, P4=2 (P2s: ANN-pull score semantics mode-dependent — rank pseudo-scores vs 1/(1+d), never cosine; 4(b) floor/top-k gating unspecified — floor-less top-k would naturally admit the sub-threshold motivating pair, partially resolving OD#1; prompt-contract "explicit-assertion-only SCOPED" ambiguity re-installs root cause #5; P3s: circuit-breaker degradation unpinned, lens resolution for retrieved old points, AC4 assertion tension in embedded, DE2E-N1 citation drift, run_vector_query vs degradation_chain ambiguity)
- Verifier B: P0=0, P1=0, P2=3, P3=2, P4=1 (P2s: AC4 assertion contradicts its own embedded carve-out — brute-force IS a full scan; ANN-pull parallel candidate path with drifted score semantics + no 0.40 gate pre-empts OD#1; two-cycle E2E lacks cross-stream + path provenance assertions; P3s: search_engine.py missing from affected-files; ≤200-cap truncation order unpinned)
- Controller action: Fixed P2s (batch-local cosine recompute for uniform verifier evidence; floor policy folded into OD#1 incl. top-k-admits-0.29 note; prompt semantics pin — judges candidate pairs, may conclude implied relations; AC4 mode-conditional; E2E assertions (a) cross-stream + (b) path provenance; search_engine.py read-only row; truncation order top-similarity default; citation fixes). Gate PASSES — clean at P0/P1, P2+ incorporated.
- **Exit: no P0s, no P1s → gate passed (2 cycles).**

---

## Phase 5.6 — Coherence Gate

`[QWEN-GATE] substitute reviewer used` — qwen3.8-max BLOCKED (401). ONE fresh-context substitute reviewer dispatched via `task` with the skill's coherence prompt.

### Coherence review result
- No P0. `[QWEN-GATE] P1-1`: gold-standard reviewer provenance — Slice 3 substituted "fresh-context agent reviewers + human adjudicator" for the problem diamond's "2 independent human reviewers" (KnowledgeNet annotator-noise evidence); agent reviewers may correlate with the verifier (same DeepSeek family) → systematically inflated measured precision. **FIXED**: reviewer independence pinned at Slice 3 (different provider OR human stratified sample OR correlation risk recorded in AC2).
- `[QWEN-GATE] P1-2`: eval-set sampling circular — "production candidate distribution" doesn't exist at Slice 3 (ANN pull lands Slice 4). **FIXED**: sampling protocol pinned at Slice 0, with the warm-up acceptance gate accepted as the primary calibration mechanism (dissolves circularity).
- `[QWEN-GATE] P2-3`: AC1 can pass without solving the motivating 0.29 pair. **FIXED**: AC1 fixture includes sub-threshold/floor-less pair per OD#1.
- `[QWEN-GATE] P2-4`: warm-up acceptance-rate gate dominates the held-out eval (simpler, real production data, doubles as calibration). **ACCEPTED** into Slice 3 as the primary calibration mechanism.
- `[QWEN-GATE] P2-5`: CI-lower-bound semantics ambiguous (Wilson LB ≥ 0.80 → observed ~0.94 at n=30 — a different quality requirement than the stated 0.80). **FIXED**: gate semantics pinned at OD#3/Slice 3.
- Verdict: diamonds hang together; no drift back to issue-literal framing; all Phase-1 dimensions traced to the plan (AQ1-5, root causes (c)/(d), EP inflation, freemium #426, draft/live, separately-ingested); research cross-check clean (every plan dep traces to Integration Docs; score-semantics + no-0.40-gate claims verified in code).

## Review Cycle Log

### problem-verify — 2 cycles, clean
- Cycle 1: Verifier A P0=0/P1=0/P2=4/P3=4; Verifier B P0=0/P1=2/P2=3/P3=3 → controller fixed P1-1 (threshold-vs-cue-gate mechanism + OD#1), P1-2 (live-endpoint tension + OD#2), 7 P2s, 4 P3s → re-dispatch.
- Cycle 2: A P0=0/P1=0/P2=0/P3=0/P4=2; B P0=0/P1=0/P2=0/P3=1/P4=2 → incorporated P3-1 (OD-resolution ordering vs close-check) → **PASS** (no P0/P1).

### solution-verify — 2 cycles, clean
- Cycle 1: A P0=0/P1=1/P2=1/P3=3/P4=3; B P0=0/P1=2(P1-1 border P0)/P2=4/P3=5/P4=1 → controller fixed P1s (Slice 4 fold-vs-cost contradiction → two-part mechanism; index-exists correction; failure policy + cross-run idempotency), 5 P2s, 5 P3s → re-dispatch.
- Cycle 2: A P0=0/P1=0/P2=3/P3=3/P4=2; B P0=0/P1=0/P2=3/P3=2/P4=1 → incorporated 6 P2s (score-semantics + floor policy pins, mode-aware AC4, E2E path assertions, search_engine.py row, truncation order, prompt-semantics pin) → **PASS** (no P0/P1).

### Coherence — 1 cycle (substitute reviewer), fixed once
- `[QWEN-GATE] P1-1` reviewer independence, `[QWEN-GATE] P1-2` sampling circularity, `[QWEN-GATE] P2-5` CI semantics → FIXED at Slice 0/3/OD#3; P2-3 AC1 fixture → FIXED; P2-4 warm-up gate → ACCEPTED. No re-dispatch (fix-once per task constraints; all fixes are Slice-0 human-gate pins).

## Phase 6 — Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| Verifier LLM call (relation decisions) | External service (LLM provider) | Slice 2 (`_RelationStage` mode; `OpenAICompatModel`/`OllamaModel` — existing, no new dep) | ✅ |
| Token usage / per-cycle cost | Cross-cutting (telemetry) | Slice 1(b) token-usage capture (provider-normalized) + Slice 6 budget assertions + OD#5 cap | ✅ |
| Candidate generation (fold + ANN pull) | In-process (existing machinery) | Slice 4(a) fold + 4(b) `run_vector_query` (search_engine.py:334, read-only) + batch-local cosine recompute | ✅ |
| Operator write path (draft lifecycle) | Data store (graph) | Slice 1(a) confirm-and-route: `EventAPI.add_operator` already drafts; EP excludes drafts (`include_draft=False`); promotion reviewer-gated (`promote_point` #785) | ✅ |
| Pair-level idempotency (within + cross run) | Data store (graph) | Slice 1(c) `seen_pairs` extension + graph-level pair-exists check | ✅ |
| Review queue (below-threshold/low-conf/live/failure) | Data store (Point properties) | Slice 5 relation `candidate_type` + payload extension (contents + lens + justification); approve→draft op + Variant-C deferral; ⚠️ dedup queue model is content-specific — extension is a wiring task | ✅ |
| LLM provider key | Env/config | Existing `OpenAICompatModel` config path — no change | ✅ |
| Precision eval harness | Testing infra | Slice 3 (exact-pair scorer; pytest; no new dep); DE2E-4 EP-draft harness reuse | ✅ |
| Branch dependency (#784/#785 machinery) | Git/merge | Slice 1(d) merge `fix/1012-session-shared-embedded-db` (promote_point + queue NOT on main) | ✅ |
| Freemium envelope (#426) | Cost | OD#5 tier gate + ≤200 candidates/cycle cap + Slice 6 report | ✅ |
| EP belief propagation integrity | Cross-cutting | Draft-status exclusion operative today; AC6; verifier writes default to draft | ✅ |
| MCP/CLI surface | API | **Out of scope** (mining tools still not exposed — separate epic concern; no change here) | ⏭️ intentionally excluded |
| Vector-index infra | Infra | **None needed** — existing HNSW index reused (read-only); embedded brute-force degradation documented | ✅ |
| Missing infra tooling | — | None — all touch points have in-repo or existing-provider coverage. Skip-with-note: none triggered. | ✅ |

**<HARD-GATE>** All wiring gaps resolved (the two ⚠️ items are scheduled Slice 1/5 tasks with named owners, not gaps). ✅

## Complexity (7-domain)

| Domain | Rating | Rationale |
|---|---|---|
| Architecture | complex | Two-part cross-stream mechanism (fold + existing-ANN pull), verifier stage mode, idempotency, cost bounds, mode-dependent (hosted/embedded) behavior |
| Ontology | standard | IMPL/NAND unchanged (ONTOLOGY §3.1); queue payload extension (relation candidate_type); no new stored kinds |
| UX | low | No GUI; review-queue interaction reuses dedup-queue pattern; human gates at Slice 0/3 |
| Data | standard | Review-queue property-model extension; eval gold set construction; no schema migration (point-property based) |
| Research | medium | Phase 1.5 artifact (6 queries); precision methodology + gold-incompleteness caveats feed Slice 3 |
| Library/Deps | low | Zero new third-party deps (verified: OpenAICompatModel, sentence-transformers, HNSW index all existing) |
| Security/Privacy | low | No new data surfaces; LLM calls carry point contents to the configured provider (existing pattern); freemium tiering decision (OD#5) |
