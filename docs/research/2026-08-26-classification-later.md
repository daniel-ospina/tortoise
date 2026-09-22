---
title: "Deep research — kinds classification-later + deterministic chain enforcement (#1695)"
type: capability
domain: capability
doc_status: live
created: 2026-08-26
issue: 1695
ownedBy: epistemic-team
subjects:
  team: epistemic-team
aboutSubjects:
- core:plan
- core:workflow
- core:occurrence
aboutObjects:
- core:Project
- dev:issue
- product-strategy:feature
---

# Classification-Later Architecture — deep research

## The question

Should the extractor's S2 step stop carrying the full kind vocabulary (~1,788 tokens for 54 kinds today, growing linearly with expansion packs) and instead emit **untyped bits**, with a **separate classification step** mapping each bit to the closed kind vocabulary afterward? Which of (a) keep in-context vocabulary, (b) extract-untyped + LLM-classify-later, (c) extract-untyped + embedding-classify-later, (d) hybrid (core kinds in-context, pack kinds classified later) wins, and at what accuracy/cost tradeoff?

Measured baseline (repo ground truth, 2026-08-13 HEAD): rendered `MASTER LIST` = 7,154 chars ≈ **1,788 tokens** (54 kinds: 16 objects + 5 subjects + 1 point + 7 events + 25 pack kinds, ≈33 tok/kind); S2 instructions+contract ≈ 2,218 tokens. The vocabulary is ~45% of the S2 prompt and is re-injected into ~265k calls.

---

## Context-grounded vs post-hoc classification (evidence)

### What the literature says about joint vs pipelined extraction

- **PURE (Zhong & Chen, NAACL 2021)** — a *pipelined* entity→relation extractor beat all prior *joint* models by **1.7–2.8% absolute F1** on ACE04/ACE05/SciERC. Mechanism: entities and relations need **different contextual representations**; sharing one encoder for both hurts. [Evidence]
- **Pipeline vs joint empirical study (Yan et al., AACL 2022)** — the systematic comparison: well-designed pipeline ≈ joint, but a fully joint model with span pruning + high-order inference still wins. "If the tasks have strong correlations, a properly designed joint approach tends to have higher performance." → the joint advantage is real but **conditional on design and task coupling**; pipeline approaches are competitive at a fraction of the engineering complexity. [Evidence]
- **RareDis E2E RE study (Gupta et al., 2023)** — pipeline models beat sequence-to-sequence models and beat GPT-family models (with **8× the parameters**) by >10 F1 points; GPT models only win in zero-shot settings. [Evidence]
- **MuSEE (Structured Entity Extraction, arXiv 2402.04437)** — decomposes extraction into **3 stages: (1) identify entities (untyped), (2) determine types, (3) predict property values**; reports that staged decomposition *improves* accuracy over one-shot extraction ("each stage leverages contextual clues for more accurate predictions") while cutting output tokens. This is literally the "extract-untyped, classify-later" architecture for entity extraction. [Evidence]
- **Decompose-Enrich-Extract (DEE, arXiv 2406.01045)** — event extraction decomposed into Event Detection + Event Argument Extraction with per-stage schema prompts; decomposed prompting improved F1 by **+8.3 (detection) and +4.64 (argument) points** on ACE05-En vs a single joint prompt carrying the full schema. [Evidence]
- **GCIE (Findings EMNLP 2024)** — two-stage: an **LLM "Recognizer"** first decides *which types are present* (type recognition as multi-label classification), then small SLM "Experts" extract spans per type. LLM-as-type-recognizer with a candidate type list outperformed fine-tuned SLMs at type recognition, especially with more examples. [Evidence]

**Takeaway:** extract-then-classify is not a degradation risk per se — it is *the state of practice* for structured extraction at scale, and in several head-to-heads it *beats* joint extraction. The failure mode to engineer against is **error propagation** (a mis-extracted bit is garbage no matter how good the classifier) — the same failure mode tortoise already accepts between S1→S2.

### Does seeing the vocabulary + source together help kind selection?

The strongest evidence that **source context matters for kind selection** comes from entity typing:

- **Ultra-fine entity typing (Choi et al., ACL 2018)** — the type of a mention is often context-dependent: "I met the movie star Leonardo DiCaprio on the plane to L.A." → type `passenger` is correct for DiCaprio, a type *unobtainable from the mention alone* or from knowledge bases. Standard UFET classifiers take **(sentence + mention)** as input — i.e., the bit *plus local context*, not the whole document. [Evidence]
- **UFET with weak supervision (Dai et al., ACL 2021)** — explicitly: "it is difficult to obtain types that are highly dependent on the context" (the passenger case); head-word supervision captures only context-free types. Consequence: **a classifier fed (bit text + its local context/quote) captures most of what matters; a classifier fed only the bit's surface string does not.** [Evidence]
- **Biomedical entity linking (PILOT, 2026; SapBERT line)** — "The surrounding sentence is frequently the only signal that disambiguates" mentions; retrieval-only (mention vs entity name in isolation) is "weakest exactly where context matters" → their production pattern is retrieval-then-**contextual reranker**. [Evidence]
- **When is the bit's own text sufficient?** Head-word/self-descriptive cases: "a famous actor" → `actor`, "the Tortoise SDK" → `product`-ish, "the PR was merged" → `occurrence`. The tortoise vocabulary is *mostly* self-descriptive (kind names are camelCase nouns with descriptions); the context-dependent minority is the **nearMiss pairs** (`useCase` vs `userJourney` vs `jobToBeDone`; `feature` vs `product` vs `requirement`) where the *role in the conversation* decides the kind. Those need either the bit's slots/quote or a small LLM adjudication step. [Hypothesis grounded in the UFET mechanism + tortoise kindDefs' `nearMisses`]

**Net:** the "context-grounded extraction" advantage is real but (i) it mostly lives in the *local context* (quote, slots, surrounding turn), which a post-hoc classifier can be handed; (ii) it applies mainly to confusable/context-dependent kinds, not the bulk of a self-descriptive closed vocabulary; (iii) no study shows that seeing a 54-kind (let alone 1,000-kind) list improves *extraction recall* of the bits themselves — the vocabulary gates **typing**, not **finding**.

---

## LLM classification into many buckets (techniques + evidence)

### The core finding: LLMs degrade as the label set grows

- **LongICLBench (arXiv 2404.02060)** — extreme-label classification, 28–174 labels, up to 50k-token demonstrations. Long-context LLMs do OK on small label sets but **near-zero on 174 labels**; models show a **recency bias toward labels at the end of the list**. [Evidence]
- **HierLabelNet (ISPRS IJGI 2025)** — geographic text classification: full label space → **39.78% micro-F1**; label space filtered to top-5 via a semantic vector DB → **62.82%**; top-10 → 59.75%; top-15 → 55.77%. The degradation with label-set size is *dramatic and monotonic*. [Evidence]
- **Label Space Reduction (LSR, Information Retrieval J. 2026)** — zero-shot classification with all labels in the prompt degrades as class count grows (attention dilution + positional bias); iteratively ranking/reducing the candidate label space improves macro-F1 by **+7.0% avg (up to +14.2%)** on Llama-3.1-70B, +3.3% (up to +11.1%) on Claude-3.5-Sonnet. [Evidence]
- **Primacy Effect of ChatGPT (Wang et al., EMNLP 2023)** — label order shuffling changed ChatGPT's prediction on **87.9% of instances** (TACRED); the model favors **earlier** labels. Fine-tuned BERT is order-stable. Companion work (**Serial Position Effects, Findings ACL 2025; option-order sensitivity, Findings NAACL 2024, 13–85% accuracy swings**) confirms label-order sensitivity is pervasive across models. [Evidence]
- **Finance classification (ACM TOIS 2024-ish)** — all models (Mistral-7B, Llama3-8B, Phi-3) collapsed below 10% on FinRed, attributed to its **29-label space** + long-tail distribution. [Evidence]
- **NICE (Srivastava et al., ACL 2024)** — the paper the question references. Its actual finding is subtly different from "big label sets hurt": with **detailed instructions (including the label space), in-context example (ICE) optimization yields diminishing returns** for plain classification; only **schema-complex generative outputs** (MTOP, Break, NL2BASH) remain ICE-sensitive. Their instruction template explicitly includes "Task Definition with Label Space (+LS)". Implication for tortoise: **the instruction side is the lever** — a detailed, label-space-bearing instruction is worth more than example selection — *but* NICE tested up to ~30 labels (TREC-6, SST-5), not hundreds. The degradation evidence above (LongICLBench, HierLabelNet, LSR) is what governs at pack scale. [Evidence]

### Technique 1 — Retrieval-based example + label selection (the dominant workaround)

- **"In-Context Learning for Text Classification with Many Labels" (GenBench@EMNLP 2023)** — for 25–150-label intent classification (BANKING77, HWU64, CLINC150, GoEmotions): give the LLM only a **retrieved partial view of the label space** per call. Set SOTA *without any fine-tuning*; ablations show the model uses (a) similarity of examples to the input, (b) **semantic content of class names**, (c) correct example-label correspondence — all to varying degrees per domain. **This is the direct precedent for "don't show all kinds, retrieve the relevant ones."** [Evidence]
- **KATE-style example selection (Liu et al., 2021, "What Makes Good In-Context Examples for GPT-3?")** — retrieving the most embedding-similar labeled examples per query beats random/fixed example sets; referenced as the ICE-selection baseline in NICE. [Evidence, via NICE citations + established result]
- **ASEE (Findings EMNLP 2025)** — schema-aware event extraction: loading all candidate schemas into the context is "suboptimal… performance decay due to 'lost in the middle'"; the fix is **schema retrieval** (paraphrased schema pool + top-k retrieval) before extraction. Also documents **schema hallucination** (LLMs invent event types not in the provided schema) — the very failure tortoise's closed-vocabulary rule exists to prevent. [Evidence]
- **Schema as Parameterized Tools (SPT, arXiv 2506.01276)** — large predefined schema pool → **schema retrieval (BM25 / BGE-M3 / BGE-Reranker, Recall@k=5)**, then schema filling; matches LoRA-finetuned baselines with 43k trainable params. [Evidence]
- **Hierarchical/iterative classification** — for deep taxonomies, layer-by-layer inference drastically reduces the candidate set per call: Retrieval-style ICL for Few-shot HTC (TACL 2024, adjacent-semantically-similar labels are the hard case); LSR's iterative reduction; LEC-KG's "relation classification that reduces the search for infrequent relations" (coarse-to-fine). For tortoise's *flat-ish* pack structure (pack → kind, 1 level deep), the two-level trick is exactly **pack-first, then kind-within-pack** — a 25-kind problem instead of a 200-kind problem. [Evidence]

### Technique 2 — Batch classification (how many items per call)

- **"Researchers waste 80% of LLM annotation costs…" (arXiv 2604.03684)** — 8 production LLMs, 3,962 tweets, batch sizes 1→1,000, 960k classifications: **6 of 8 models stayed within 2pp of the single-item baseline through batch size 100** (saving >80% of token cost); degradation at b≥250 was model-specific; some tasks *improved* at larger batches (co-presented items provide distributional calibration). [Evidence]
- **Multi-Instance Processing (ACL 2026)** — 16 LLMs: slight degradation at ≈20–100 instances, **collapse beyond ~200–1,000**; the *instance count* (not context length) drives degradation (Spearman −0.61 vs −0.37). [Evidence]
- **Batch size for requirements classification (Utrecht, 2025)** — optimal batch size is model+dataset dependent; batch=1 is *not* a great default (highest variance); DeepSeek-family quality classification improved up to b=32. [Evidence]
- **Multi-problem evaluation (ACL 2025 Insights)** — LLMs handle *homogeneous batch classification* fine, but fail at "select indices of text falling into each class" — so ask for **per-item labels in a JSON array**, never index-selection. [Evidence]

### Technique 3 — Few-shot example budget

- More shots do **not** monotonically help: NICE (diminishing returns with good instructions), "Instruction Tuning vs ICL in CSS" (arXiv 2409.14673, 1→32 shots often *declines*), "100 Labelled Samples to Break Even" (EMNLP 2025: small fine-tuned models beat general LLMs with ~100 labels). For classification-later, examples per kind are best **retrieved per item** (technique 1) rather than fixed. [Evidence]

---

## Embedding-based classification (the cheap path)

### The mechanism and the numbers

Classify by **cosine similarity between the bit's embedding and each kind's embedding** (kind = description + synonyms + examples — exactly what tortoise `kindDefs` already carry). No LLM per item: a CPU-friendly encoder scores all N kinds in one pass.

- **BTZSC zero-shot benchmark (ICLR 2026)** — 22 datasets, 38 checkpoints across 4 families: strong embedding models (GTE-large-en-v1.5) **close most of the LLM gap at a fraction of the latency**; NLI cross-encoders plateau; instruction-tuned LLMs (4–12B) lead only on topic classification; **rerankers** (Qwen3-Reranker-8B, 0.72 macro-F1) are SOTA. → "embedding model as first pass, reranker/LLM as second pass" is the winning composition. [Evidence]
- **"The Embedder's Dilemma" (arXiv 2608.12875)** — 10 LLMs vs 26 embedding models on 37 tasks: **embeddings lead on classification by ~5.6 points** (kNN vs zero-shot LLM), and **the gap widens on fine-grained, many-class tasks** (Banking77/77 classes: SFR-2 90.8 vs GPT-class 85.2); LLM quality at 10–100× the cost. [Evidence]
- **RaLP (arXiv 2212.10391)** — sentence-encoder + **retrieval-augmented label prompts**: ST5-XXL (4.8B) beat GPT-3 175B-class baselines on closed-set zero-shot classification; removing the label-prompt retrieval component cost 6.2 points on AGNews. → **label description quality and enrichment are the lever for embedding classification.** [Evidence]
- **Zero-shot topic inference (Sarkar et al., 2023)** — **topic keywords substantially improve over topic names alone** for embedding-similarity classification. Tortoise's kindDefs already include `synonyms` + `examples` + `nearMisses` — the exact enrichment that moves embedding classification from "name matching" to "description matching". [Evidence]
- **Biomedical normalization — the thousand-to-million-class production precedent** — SapBERT self-alignment over UMLS (4M+ concepts) → SOTA on 6 biomedical entity-linking benchmarks (NAACL 2021); kNN over SNOMED CT concept embeddings → F1 0.853 across ~350k concepts (2023); UMLS embedding retrieval (3.59M CUIs) → 0.858 accuracy clinical, up to 0.980 with an LLM generating preferred terms first (HEALING@ACL 2026). Meanwhile **GPT-4/DeepSeek-R1 *directly prompted* for concept normalization trails specialized retrieval by wide margins** (PILOT, 2026), and GPT models score <50% accuracy coding ICD-9/10/CPT from descriptions (Soroush et al.) — evidence that *generative prompting does not scale to very large label spaces at all*. [Evidence]

### The honest caveats

- **NearMiss confusability**: embedding similarity is coarse. Tortoise's `nearMisses` pairs (product/feature, useCase/userJourney/jobToBeDone, plan/goal/target) are exactly the cases where top-1 cosine is unreliable. Mitigation: threshold + **defer to LLM adjudication** on low-margin cases; or rerank top-5 with a cross-encoder (BTZSC shows rerankers are the accuracy leader). [Evidence-based design, hypothesis on tortoise's specific pairs]
- **Embedder matters**: bge-small-en-v1.5 (tortoise's current embedder, 384-dim) is a workhorse, not a leader; BTZSC/Embedder's-Dilemma used GTE-large / SFR-2 class embedders. Budget permitting, test an upgrade (e.g., GTE-large or bge-m3) before concluding. [Hypothesis]
- **Silent errors**: an embedding classifier never reasons; a wrong kind is confident and quiet. Need the confidence threshold + the existing `enforcement: warn|retry|block` machinery to catch low-confidence assignments. [Design note]

---

## The synthesis: which approach for our case

### The decision-relevant facts about tortoise's workload

1. **Only ~3 kind dimensions are actually classified**: entity kinds (objects+subjects, 21 core + pack kinds), event kinds (7 core + pack), slot kinds. `pointKind` is hard-coded `statement` (v3.7) — the biggest kind surface (points) needs no classification at all.
2. **The vocabulary is closed** and every kind already has `description + synonyms + examples + nearMisses` — the exact label-enrichment that makes both LLM-with-descriptions and embedding classification work (RaLP, topic-keywords, SPT, HierLabelNet evidence).
3. **Cost is the driver**: 265k calls × ~1,788 vocab tokens; and per the batch-evidence, classification-later can amortize its own prompt over 25–100 items.
4. **At 54 kinds today, in-context is probably fine**; the problem is the *trajectory* — LongICLBench (174 labels → near-zero), HierLabelNet (full-space 39.8% vs top-5 62.8%), and the primacy/position-bias work say the curve gets steep well before "thousands."

### Verdict on the four options

| Option | Kind accuracy at 54 kinds | Kind accuracy at 300+ kinds | Marginal cost per 1k calls | Risk profile |
|---|---|---|---|---|
| **(a) keep in-context vocab** | best today (context-grounded; but unmeasured vs (b)/(c)) | **collapses** (LongICLBench, HierLabelNet) + position bias | +1.8M vocab tokens (today) → +10M+ at 300 kinds | vocabulary becomes the dominant prompt cost; label-order bias (primacy) enters |
| **(b) untyped + LLM-classify-later** | ≈(a) if classifier sees bit+quote+slots; worse if bit-only (UFET context evidence) | degrades too — the classifier still needs the label space; works only with retrieval-filtered candidates or hierarchy | vocab tokens move to the classify step; **batch 25–100 cuts the per-item overhead ~80%** | error propagation (S2 untyped → classify); batch collapse >200 items |
| **(c) untyped + embedding-classify** | good on well-separated kinds; weak on nearMiss pairs; needs threshold/rerank | **scales to thousands for free** (biomedical precedent: 0.85+ F1 at 100k–1M concepts) | ~0 LLM tokens; one embedder pass per bit + one per kind (amortized) | silent errors on confusable kinds; embedder quality ceiling |
| **(d) hybrid: core kinds in-context, pack kinds classified later** | ≈(a) (core is what conversations mostly emit) | **best of the field**: retrieve top-5–10 candidate pack kinds per bit (embedding), LLM classifies among candidates (ASEE/SPT/HierLabelNet/GenBench-many-labels all do exactly this) | S2 prompt shrinks to core-only (~25–40% vocab reduction today, unbounded as packs grow); classify step is small (top-10 candidates) and batchable | complexity: two moving parts; retrieval recall gates everything (top-k must contain the true kind) |

**Recommendation: (d), in two tiers — and (c) as the inside-tier classifier.**

- **Tier 1 (LLM tier):** S2 keeps **core kinds in-context** (objects/subjects/events ≈ 30 kinds, ~1,000 tokens — the bulk of what any conversation emits). Pack kinds are **not** in the S2 prompt. When S2 wants to emit an untyped bit whose kind isn't core, it emits `kind: null` + the bit text + quote + slots.
- **Tier 2 (classifier):** per untyped bit, (i) **embedding retrieval** over all pack kinds (bge-small today; the kind embedding = description+synonyms+examples, recomputed on pack install — a one-time cost); (ii) if top-1 margin ≥ calibrated threshold → assign (optionally with a **cross-encoder rerank of top-5** for nearMiss safety); (iii) if below threshold → **batch LLM adjudication among the top-5–10 candidates** (batch 25–50 per call, with kindDefs for only those candidates). This is the state of practice *verbatim*: retrieve → rank → adjudicate (PILOT, ASEE, SPT, HierLabelNet, LSR, GenBench-many-labels).
- **Why not (b) alone:** (b) just moves the label-space problem to a second prompt — at 300 kinds it hits the same degradation, minus the extraction context. It's only the *combination* of retrieval-filtering + batching that makes LLM-classify-later cheap and accurate at scale.
- **Why not (c) alone:** the nearMiss/context-dependent minority (useCase vs userJourney; feature vs product) needs *some* reasoning; a pure cosine classifier will be silently wrong on exactly the pairs the ontology authors flagged as confusable. Embeddings decide the cheap 90%; an LLM adjudicates the ambiguous 10%.

### The accuracy/cost tradeoff, honestly stated

**What you gain with (d):**
- S2 prompt: ~1,788 → ~1,000 vocab tokens today (and the vocabulary share of the prompt *stops growing* as packs ship). At 300 kinds (hypothetical ~10k vocab tokens in-context), (a) is dead on cost and accuracy; (d) is unchanged.
- **Kind accuracy at pack scale *improves*** over (a): the classifier sees 5–10 candidate kinds with full descriptions instead of 300 in a flat list (HierLabelNet: +23 points from filtering; primacy bias removed by never listing the whole space).
- Classification-later enables **per-kind enforcement** (warn/retry/block), **calibrated confidence**, and a **cheap retrain loop** (wrong kind → add to kind's examples → re-embed) that in-context prompting cannot offer.

**What you give up / must pay for:**
- **Error propagation + context loss**: the classifier sees bit+quote+slots, not the whole conversation. For context-dependent kinds this is a real regression *if* you don't pass the local context (UFET evidence). Pass the bit's quote/source span and slots into the classifier; measure the delta.
- **Latency**: +1 embedding pass per bit (fast; the repo already runs bge-small with calibrated thresholds) and, for the ambiguous minority, an extra LLM call. Amortized over batching, small.
- **Extraction-recall risk (unmeasured)**: today the vocabulary may anchor S2's *what counts as durable* judgments. No direct evidence on this; OpenIE evidence suggests open (untyped) extraction finds *more* surface facts but with noise — tortoise's mechanics-token VALUE FILTER is instruction-driven, not vocabulary-driven, so the risk is modest, but it must be measured (below).
- **Position-bias risk in the current system**: even staying with (a), the primacy-effect evidence says label order corrupts kind selection at scale — add label-order randomization to any A/B to quantify how much of today's kind noise is pure list-order bias.

---

## Proposed experiments (a concrete 200-item A/B)

**Goal:** quantify the kind-accuracy delta and cost delta of classification-later before committing the pipeline.

**Corpus:** 200 conversation stories drawn from the real S1 output stream (stratified across the pack activations in production). Ground truth: (1) run today's S2 → take its entity/event/slot kind assignments; (2) a reviewer pass (LLM adjudicator with the kindDefs + source text, or the owner) corrects ~200 sampled bits — target ≥200 *bit-level* gold labels (bits ≈ entities+events+slots emitted, typically 5–15 per story, so ~200 stories gives 1,000+ bits; label the first 200–400).

**Arms (same 200 stories, same S1 input):**
- **A (baseline):** today's S2, full vocab, as-is. Record kinds + cost (tokens/call).
- **A′ (order shuffle):** same as A but vocabulary order randomized per call → measure kind-change rate. This is a *free* diagnosis of position bias in the current system (primacy-effect protocol).
- **B (untyped + LLM-classify-later):** S2 without the vocab block (emit `kind: null` bits + quote + slots); classifier = LLM per bit with the *full* vocab. Sweep batch size {1, 25, 50} (batch evidence: safe to 100; JSON-array output, never index selection).
- **C (untyped + embedding-classify):** S2 without vocab; classifier = bge-small cosine over kind embeddings (description+synonyms+examples), top-1 with threshold; nearMiss-margin cases flagged.
- **D (hybrid):** S2 with core-only vocab; pack-kind bits classified via retrieve-top-5 → LLM adjudicate among top-5 (batch 25). Optionally D′: embedding top-1 accepted when margin high, LLM only on the ambiguous tail.

**Metrics:**
1. **Kind accuracy** per arm, per surface (entity/event/slot), macro-F1 + confusion matrix over the gold bits. Report the nearMiss-pair accuracy separately (that's where the arms will diverge).
2. **Bit recall**: did B/C/D's untyped S2 still emit the same bits as A? (The "does the vocabulary anchor extraction" question.)
3. **Cost per 1k calls** (input tokens), **p95 latency**, and for B: token cost vs batch size.
4. **Confidence calibration** of the classifier tier (does the margin/threshold actually predict error?).

**Decision rules:** if D's kind accuracy ≥ A within ~2–3 points *and* D's cost is ≥30% lower, ship D (with C as the cheap tier inside D). If B/C/D lose >5 points on kind accuracy or >3 points on bit recall, the vocabulary is doing anchoring work that outweighs the savings — stay on (a) and instead fix the known cost/accuracy leaks: trim vocab descriptions, dedupe nearMisses, and randomize label order. Publish the confusion matrix — it is the evidence the pack authors need to write better `nearMisses`.

---

## Sources (URLs)

**Label space / many-bucket classification**
- NICE (Srivastava et al., ACL 2024): https://aclanthology.org/2024.acl-long.300/ · https://arxiv.org/abs/2402.06733
- In-Context Learning for Text Classification with Many Labels (GenBench 2023): https://aclanthology.org/2023.genbench-1.14/ · https://arxiv.org/abs/2305.15744
- LongICLBench (arXiv 2404.02060): https://arxiv.org/abs/2404.02060
- HierLabelNet (ISPRS IJGI 2025): https://doi.org/10.3390/ijgi14070268
- Label Space Reduction LSR (Inf. Retrieval J. 2026): https://link.springer.com/article/10.1007/s10791-026-10420-6
- Primacy Effect of ChatGPT (EMNLP 2023): https://aclanthology.org/2023.emnlp-main.8/ · https://arxiv.org/abs/2305.12687
- Serial Position Effects of LLMs (Findings ACL 2025): https://aclanthology.org/2025.findings-acl.52/
- LLM Sensitivity to Order of Options (Findings NAACL 2024): https://aclanthology.org/2024.findings-naacl.130/
- Retrieval-style ICL for Few-shot HTC (TACL 2024): https://aclanthology.org/2024.tacl-1.67/
- Position Bias in Ordinal Classification (2026): https://arxiv.org/abs/2608.08869
- 100 Labelled Samples to Break Even (EMNLP 2025): https://aclanthology.org/2025.emnlp-main.9/

**Batch classification**
- Researchers waste 80% of LLM annotation costs (arXiv 2604.03684): https://arxiv.org/abs/2604.03684
- Multi-Instance Processing degradation (ACL 2026): https://aclanthology.org/2026.acl-long.1470/
- Batch size for requirements classification (Utrecht 2025): https://research-portal.uu.nl/en/publications/one-size-does-not-fit-all-on-the-role-of-batch-size-in-classifyin/ · https://chuniversiteit.nl/papers/classifying-requirements-using-llms
- Multi-problem evaluation (ACL 2025 Insights): https://aclanthology.org/2025.insights-1.12/

**Pipeline vs joint extraction**
- PURE (Zhong & Chen, NAACL 2021): https://aclanthology.org/2021.naacl-main.5/ · https://arxiv.org/abs/2010.12712
- Pipeline vs Joint empirical study (AACL 2022): https://aclanthology.org/2022.aacl-short.55/
- RareDis E2E RE comparison (2023): https://arxiv.org/abs/2311.13729
- MuSEE / Structured Entity Extraction: https://arxiv.org/abs/2402.04437
- DEE: Decompose, Enrich, Extract (2024): https://arxiv.org/abs/2406.01045
- GCIE (Findings EMNLP 2024): https://aclanthology.org/2024.findings-emnlp.4/

**Schema-aware / retrieval-first extraction**
- ASEE (Findings EMNLP 2025): https://aclanthology.org/2025.findings-emnlp.419/ (pdf: .../anthology-files/pdf/findings/2025.findings-emnlp.419.pdf)
- Schema as Parameterized Tools SPT (2025): https://arxiv.org/abs/2506.01276
- Adaptive RL Planning for IE (2024): https://arxiv.org/abs/2406.11455
- Open IE vs traditional RE (Banko & Etzioni, ACL 2008): https://aclanthology.org/P08-1004/
- Linking Surface Facts to KGs (EMNLP 2023): https://aclanthology.org/2023.emnlp-main.445/
- LEC-KG (2026): https://doi.org/10.48550/arxiv.2602.02090

**Entity typing (context-dependence + large type sets)**
- Ultra-Fine Entity Typing (Choi et al., ACL 2018): https://aclanthology.org/P18-1009/
- UFET with weak supervision from MLM (ACL 2021): https://aclanthology.org/2021.acl-long.141/
- LITE — UFET as NLI (TACL 2022): https://aclanthology.org/2022.tacl-1.35/
- UFET with prior knowledge about labels (2023): https://arxiv.org/abs/2305.12802
- CASENT — seq2seq for 10k types (Findings EMNLP 2023): https://aclanthology.org/2023.findings-emnlp.1040/
- Neural-PCRF for UFET (EMNLP 2022): https://aclanthology.org/2022.emnlp-main.459/

**Embedding-based classification**
- BTZSC zero-shot benchmark (ICLR 2026): https://arxiv.org/abs/2603.11991 · https://mlanthology.org/iclr/2026/aarab2026iclr-btzsc/
- The Embedder's Dilemma (2026): https://arxiv.org/abs/2608.12875
- RaLP (2022): https://arxiv.org/abs/2212.10391
- SapBERT (NAACL 2021): https://aclanthology.org/2021.naacl-main.334/
- SapBERT-based SNOMED CT normalization (2023): https://doi.org/10.3233/shti230278
- Multistage LLM biomedical normalization (2024): https://arxiv.org/abs/2405.15122 · https://www.cambridge.org/core/journals/research-synthesis-methods/article/... (D9E5642FD2687DD9CF120865344421F6)
- Normalizing Health Concepts with embeddings + LLM (HEALING@ACL 2026): https://aclanthology.org/2026.healing-1.15/
- PILOT neighborhood-aware BioEL (2026): https://arxiv.org/abs/2608.04144
- Cost-aware model selection: encoders vs LLM prompting (2026): https://arxiv.org/abs/2602.06370
- Text Classification in the LLM Era (2025): https://arxiv.org/abs/2502.11830
- Instruction Tuning vs ICL few-shot CSS (2024): https://arxiv.org/abs/2409.14673

**Tortoise repo (grounding)**
- tortoise/extractor_v2.py — S2_TMPL, build_master_list, _render_master (measured: master list 7,154 chars ≈ 1,788 tokens; template ≈ 2,218 tokens)
- tortoise/embeddings.py — BAAI/bge-small-en-v1.5 (384-dim), calibrated thresholds (0.72 default / 0.89 near-dup)
- tortoise/model_adapters.py — deepseek-v4-flash (temp 0) via OpenRouter / DeepSeek-direct / Venice
- docs/ONTOLOGY.md — v3.7: pointKind = statement only; legacy write kinds; pack kindDefs (description/synonyms/examples/nearMisses/extractable/storeAs/enforcement)
