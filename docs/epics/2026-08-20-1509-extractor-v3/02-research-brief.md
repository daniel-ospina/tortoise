---
title: "Epic Research Brief — Extractor V3 (epic #1509)"
type: plan
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-20
aboutSubjects: tortoise
aboutObjects: extractor
---

# Epic Research Brief — Extractor V3 (epic #1509)

> **Findings date:** 2026-08-20
> Sources: the 12-report corpus (6 category re-reviews `/tmp/v3-review/`, 3 setup audits `/tmp/v3-setup/`, 3 syntheses `/tmp/v3-synth/`), the v2 measurement data (`/tmp/lme-v2-full.json` etc.), web research 2026-08-20 (Mem0/Letta/Graphiti/Hindsight market + adversarial benchmarks), ODP/ontology-design literature.

> **Dedup against PRIOR_RESEARCH (12-report corpus):** the following question sub-areas were answered by the prior corpus and NOT re-queried (per PRIOR_RESEARCH dedup): (a) per-category v2 results analysis + attribution — `/tmp/v3-review/01..06`; (b) production wiring / pipeline correctness / measurement-harness audits — `/tmp/v3-setup/01..03`; (c) prioritization / validity / architecture syntheses + competitor mechanism research (Mem0 ADD/UPDATE/DELETE, Graphiti validity windows, Letta tiers, Hindsight typed facts) — `/tmp/v3-synth/01..03` + `docs/drafts/2026-08-20-extractor-v3-category-reports/`. Fresh queries on 2026-08-20 covered ONLY the gaps the corpus left open: (d) market/positioning landscape (Mem0/Letta/Graphiti pricing, Zep CE deprecation) and (e) adversarial controlled-ablation evidence (Verbatim-Chunks, MemDelta, LightMem, UnifiedMem) — see Raw Notes.

### Strategy Context

- **Agent memory is the least-standardized layer in the agent stack, and the three incumbents make incompatible architectural bets** (2026-08-04 DigitalApplied; synix source-level analysis): Mem0 = extraction-based flat facts (vector store, optional graph; ADD/UPDATE/DELETE/NOOP reconcile); Zep/Graphiti = bi-temporal knowledge graph (every fact carries `valid_at`/`invalid_at`; contradicted facts invalidated, never deleted); Letta = agent-OS memory tiers the agent edits itself. Adopting one is an architecture decision, not a feature pick.
- **Market movement:** Mem0 raised $24M Series A (early 2026), ~62k stars, "safe default"; Zep's Community Edition is **deprecated — Zep is now hosted-only; self-hosting means building on the Graphiti engine yourself** (self-host gap = open lane); Letta is an agent runtime, not a bolt-on; Hindsight (Vectorize) leads with benchmark/retrieval quality on Postgres + pgvector, typed facts incl. **opinion (beliefs with confidence scores)** and **observation (background consolidation)**; Cognee = self-hosted graph pipeline; Mnemoverse = cross-tool memory with importance scoring + Hebbian associations + outcome feedback (memory_feedback re-ranks recall); LangMem/LlamaIndex = ecosystem-native. Pricing units are incomparable (Mem0 per-request, Zep per-credit-per-byte, Letta per model usage + CPU-sec).
- **The memory-vs-RAG framing:** memory layers and RAG are complementary; a memory layer earns its token/latency cost **where facts mutate** (pricing, policy, account status, stated preferences) — similarity retrieval happily returns the stale fact with full confidence. This is exactly the KU/supersession problem Tortoise is betting on.
- **Tortoise's positioning:** epistemic layer (Points/EP confidence/NAND/MITIGATES/supersession), Subject/Event/Object ontology, provenance. **No competitor has reader-side abstention/uncertainty** — Mnemoverse's importance/feedback and Hindsight's opinion-confidence are the closest, neither abstains. Whitespace is real (validated again 2026-08-20).
- **Adversarial counter-positioning:** the graph layer may not win flat IE recall (see UX/Workflow sections — extraction doesn't beat verbatim on IE); the defensible claim is *change, absence, and attribution* — KU/TR/MSR/Abstention — where flat fact stores and naive RAG both fail (MemDelta: full-context wins KU 72% vs 63% because conflicting-fact reasoning is not a retrieval problem).

### UX Pattern Research

- **How memory surfaces to the answering model:** Mem0 injects a ranked list of extracted facts into the prompt; Letta pages memory tiers in/out of context; Graphiti renders facts with validity windows + no LLM summarization on the read path; Hindsight ranks typed facts with confidence; **Tortoise's reader = type-fragment prompts + rendered context** (the LME reader) — the #1366 type fragments demonstrably moved answers (2/4 Abstention wins came from the TR fragment's abstain clause).
- **Anti-pattern (measured): context bloat.** v2 fed the reader ~35k tokens of whole-session transcripts (4.4× baseline) → reader refusals/hallucination under flood (9/18 TR losses had full session recall yet failed). LightMem reproduction: constructed-memory advantage **vanishes as the answering budget grows** (~330 tokens: +5.5pp; ~935 tokens: small disadvantage). Lesson: compact points win only under tight context budgets — cap + structure context, don't flood.
- **Anti-pattern (measured): answer shape.** The LME judge rewards clean abstentions and rejects partial answers (absence-note + decoy-commit) — the abstention "win" is partly answer cleanliness. Reader hygiene is product value (trust), not just benchmark.
- **Evidence-presentation driver:** xMemory/T-Mem line (from the 6-category corpus): context presentation (evidence highlighting) is an independent accuracy driver — decoration (`[SUPERSEDED BY]`, `[valid …]`, absence signals) is UX.
- **Benchmark-integrity constraint:** never feed the reader the `_abs` label (question-type flag goes to the judge only) — abstention must be inferred from evidence (absence signal, NAND/supersession markers). Cue design must be evidence-derived, not label-derived.

### Workflow Pattern Research

- **Write-path patterns (all LLM-heavy, cost grows with conversation volume, not query volume):** Mem0 = two-phase (extract candidate facts per message pair → reconcile against existing by vector similarity, pick ADD/UPDATE/DELETE/NOOP); Graphiti = per-episode extraction, **3+N LLM calls per episode** (entity extraction, resolution, edge extraction, per-edge dedup + contradiction detection) with deterministic-then-LLM entity resolution; Letta = sleep-time consolidation agent (runs a larger model off the latency path, abstracts patterns, resolves contradictions). **Tortoise v2 = 5-stage (S1 story → S2 map → S3 search → S4 gap review → S5 deterministic embed)** — the LLM-front/deterministic-back shape matches consensus; the cost lesson: collapse to ≤2 LLM calls/session and bound generation (v2's uncapped S1 calls caused the timeouts).
- **Contradiction/supersession is a write-time job** (Graphiti dedupe-at-ingest; Mem0 reconcile) — not a read-time retrieval fix. Our supersession machinery exists but is computed-and-dropped at 3 points (write trigger, ingest boundary, read decoration) — the workflow fix is wiring, not invention.
- **Eval workflow discipline (MemDelta, controlled-protocol lesson):** vary ONE component at a time; fix embedding model across comparisons; stratify by model family; report write-path cost. Applies directly: pin reader+judge, record leg-mix, gate on extraction health.
- **Micro-test-before-formal** (owner decision): tunable params (chunk granularity) get a 3-point local sweep before the formal run — cheap insurance against committing to a bad knob.

### Tech Stack Research

- **Retrieval consensus (all competitors converge):** BM25-OR-tolerant sparse + dense embeddings + entity linking + cross-encoder rerank + MMR diversity + temporal scoring; Graphiti ships 16 search recipes (BM25/cosine/BFS, RRF/MMR/cross-encoder rerankers); Zep hybrid = embeddings + BM25 + graph traversal, no read-path LLM.
- **The controlled-evidence verdict on representation (2026-08-20, three independent papers):**
  - *Verbatim Chunks Beat Extracted Artifacts* (arXiv 2601.00821): in a fixed pipeline, verbatim chunks beat LLM-extracted artifacts by **15.9pp (LoCoMo) / 22.0pp (LongMemEval-S)**; a 1-hop semantic graph does NOT recover the gap; **the union (chunks ∪ artifacts) matches chunks** (artifacts stay accuracy-neutral once verbatim text is present). Mechanism: lossy distillation — "structure should augment verbatim text, not replace it."
  - *Does Memory Need Graphs?* (ACL 2026, UnifiedMem): graph methods beat flat indices on LongMemEval when configured well (entity descriptions > similarity edges; activation-then-expansion); similarity-graph construction is hard to make effective; config detail dominates.
  - *MemDelta* (arXiv 2606.29914): one embedding swap shifts accuracy +6.2pp and flips the Mem0-vs-RAG conclusion; verbatim RAG ≈ full-context; **full-context wins KU (72% vs 63%)** — conflicting-fact reasoning is not solved by retrieval alone; agent self-memory (42%) < basic retrieval (47%).
  - *LightMem reproduction*: retriever choice swings 58.1→75.5pp; construction loses answer-relevant info (oracle gap 11.3pp); constructed memories help only under tight token budgets.
  - **Design conclusion: our two-tier (raw chunks + extracted points) IS the union architecture the evidence favors** — raw chunks are the recall floor, extracted points the epistemic surface; success criteria must NOT expect extraction to beat verbatim on flat IE — expect it to win where change/absence/aggregation matters (KU/TR/MSR/Abstention).
- **Embeddings:** local all-MiniLM-L6-v2 already implemented (`tortoise/embeddings.py`, calibrated thresholds 0.40/0.75, measured 2026-08-07); was absent in the eval env (sentence-transformers not installed → `EmbeddingModel.get() → None` → vector leg silently degraded). MemDelta: embedding choice is a ±6pp confound — keep it fixed across compared cells.
- **Sparse:** hybrid engine exists (RRF over FTS + vector + structural + TF-IDF fallback, circuit breaker); FTS is strict-AND RediSearch (`queryNodes`) — paraphrase-hostile; TF-IDF fallback only in embedded graphs. Eval currently measures two different sparse stacks per graph size.
- **Real backend:** eval must run real FalkorDB + real FTS index + embedder + structural `kind` — "test the real tortoise" (owner, critical). The v2 eval's embedded per-question graphs measured a degraded stand-in.
- **Provider:** DeepSeek direct (api.deepseek.com, `deepseek-v4-flash`/`-pro`, `DEEPSEEK_API_KEY`) proven in the eval adapter; OpenRouter collapses under concurrent load (476/500 network failures in the earlier run); production = DS-direct primary + OR fallback (owner-confirmed). Budget ≈ $15–40/run extractor+reader + $10–20 judge (gpt-4o).
- **Ontology (facts as Points):** our OWN ontology already resolved the "where do facts live" question (§3.1: evaluations of subjects are Statements (Points) with EP confidence, not edges; Facts = confidence 1.0; `aboutSubject` edges; `user` is an Object subclass) — state-value facts as Points (option A, owner-approved) reuses EP confidence, provenance, supersession, subject resolution. Competitor cross-check: Graphiti puts facts on typed edges (contradicts our principle); EverMemOS has a distinct "preferences" type (a pack-style extension we deliberately avoid per owner's over-extension concern); Hindsight's opinion-with-confidence ≈ our EP-confidence-on-Points shape.

### Assumptions Register

| # | Assumption | Confidence | Source | Validation Plan |
|---|---|---|---|---|
| A1 | The 12-report corpus is accurate (v2 run void; code-level truths stand) | high | 3 independent syntheses re-derived the arithmetic | Already validated (recomputed in /tmp/v3-synth/02-validity.md) |
| A2 | Fixing the known issues yields measurable improvement on a valid run | medium | mechanisms code-verified, impact unmeasured | Success judged on absolute per-category success criteria (as in A9); the V3 run sets the V4 baseline |
| A3 | Real-backend eval (real FalkorDB + FTS index + embedder + structural kind) is feasible in the eval env | medium | infra availability TBD | Pre-flight check in Phase 1 (M/P items) |
| A4 | The ontology needs no changes (state-value facts = Points, option A) | high | ONTOLOGY §3.1 + owner decision | Plan substeps (data model) — verify no new kinds minted |
| A5 | DeepSeek-direct is production-viable | high | adapter proven in eval | Provider pre-flight ping (M2) |
| A6 | Per-run API budget ($25–60) is secured | medium | v2 died on billing (21,342× 402) | Pre-flight billing probe (M2) before every run |
| A7 | Team capacity to land M+P+E/R/A while production v2 stays live | medium | epic-align phase decision + owner capacity statement (2026-08-20) | Cut order if binds: R2/R5 first, then E5; M+P never cut |
| A8 | The union design (raw chunks + extracted points) beats points-only and matches raw-only on the valid run | medium | Verbatim-Chunks arXiv 2601.00821 (union matches chunks) | V3 run: compare raw-only vs union on shared qids |
| A9 | Extraction value concentrates in KU/TR/MSR/Abstention, not flat IE | medium | MemDelta KU 72% full-context; IE is wording-sensitive | Per-category success criteria (IE not expected to beat raw) |
| A10 | Reader pinning + leg-mix recording makes the run interpretable | high | MemDelta controlled-protocol lesson | Run methodology records reader/prompt/leg-mix per question |
| A11 | Supersession wiring (not invention) yields KU gains | medium | machinery exists, dead at 3 points | KU category on the V3 run |
| A12 | Retry-then-fix protocol keeps the run alive without publishing garbage | high | owner decision; harness integrity reporting | Run protocol + integrity report block |

## Raw Notes

- 2026-08-20 — `/tmp/v3-review/01..06` — the 6 category re-reviews re-derived the arithmetic; key corrections: no S5 crash, 0×429/21,342×402, MSR +2.07pp not +2.53pp, Abstention p=0.375 not significant, 52 questions wrote 12,085 points but 51/52 had zero evidence marks (second mechanism beyond the 402: the ≥0.4 threshold is miscalibrated for summarizing extractors, fired 1/12,085).
- 2026-08-20 — `/tmp/v3-setup/01..03` — production wiring audits: silent partial capture (200 `extracted:0` on dead key), provider gate/consumer mismatch (gate accepts keys the adapter can't use), working tree 82 commits behind main; pipeline audit: `_complete` has deadline but no retry, no 4xx fail-fast, uncapped generations; harness audit: no integrity gate, report strips the smoking-gun fields, `outcomes_to_report → None` dead code on main (4acb47d4), `reader_prompt_hash` hashes a context-format constant.
- 2026-08-20 — `/tmp/v3-synth/01..03` — syntheses: convergence map (20 fixes, keystones: integrity gate ×7, pre-flight ×8, evidence-marking ×6, reader pinning ×5), validity ledger (63 claims status-tagged), architecture (two-tier design, competitor-stack retrieval, abstention differentiator).
- 2026-08-20 — web: Mem0/Letta/Zep architecture + pricing map (digitalapplied.com, dreaming.press, memnexus.ai, dev.to, hamzashabbir.dev); Zep CE deprecated → self-host gap.
- 2026-08-20 — web (adversarial): Verbatim Chunks Beat Extracted Artifacts (arXiv 2601.00821) — union matches chunks, artifacts alone forfeit 22pp; Does Memory Need Graphs (ACL 2026) — graph > flat under right config, config detail dominates; MemDelta (arXiv 2606.29914) — embedding swap ±6.2pp flips conclusions, KU needs conflicting-fact reasoning; LightMem reproduction — construction loses info, wins only under tight budgets.
- 2026-08-20 — web: ODP/extension-pack literature (reification vs EAV; Palantir extend-not-modify core) — supports facts-as-Points + no new pack.
