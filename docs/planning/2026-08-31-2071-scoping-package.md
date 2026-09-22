# Scoping Package — Issue #2071: Make the 3 SSP long-gold questions gradeable

**Issue:** #2071 (tortoise repo) · **Team:** epistemic-team · **Complexity:** standard
**Epic:** docs/plans/2026-08-29-1987-ask-reader.md · **Research:** docs/runbook/1987-ask-abstention-check.md
**Scoped:** 2026-08-31 · **Method:** full issue-scoping double-diamond (problem diverge → converge → external research → codebase explorer → solution diverge → converge → wiring check)
**NOT posted to GitHub** — package delivered to /tmp/scoping-2071.md only.

---

### Confirmed Problem

The measurement judges used on the product-lane QA surface — the word-overlap bar `max(2, len(gold_words)//2)` on UNIQUE words (`tools/ask_spotcheck.py:85-88` — d6233ab6 needs 28/56 unique matches, 1d4e3b97 23/47, b0479f84 24/49) and the full-gold substring containment rule (`MockJudge.judge` → `grade_label`, `tools/longmem_eval/judge.py:371-381`) — are **structurally unreachable for the 3 SSP long-gold questions** (d6233ab6, 79-word gold; 1d4e3b97, 68-word gold; b0479f84, 63-word gold), so a correct-but-paraphrased reader answer is scored wrong. This is a **measurement defect, not an answering defect**: no model upgrade fixes it, and the benchmark's own official judge (the gpt-4o LLM anscheck judge, `LLMJudge` in the same `judge.py`) is semantic and already in the repo — it just is not wired to the spot-check/CI surfaces that use the broken deterministic bars.

---

### Alternative Framings (problem-diverge)

**(a) The real problem is the similarity metric, not the questions.** Word-overlap/substring containment is wrong for ANY length — a paraphrase never guarantees exact-word overlap, and long golds just expose it. *Evidence:* external research confirms term-overlap metrics (BLEU/ROUGE) fail on paraphrased long-form answers; the official LongMemEval rubric is semantic by design. *Counterweight:* the containment judge is precise and deterministic on SHORT golds (verbatim-matchable), and the issue's indicator 2 explicitly demands preserving that precision — so the critique is adopted only for the long-gold class, not wholesale.

**(b) The real problem is the questions (fixture design).** The SSP golds are rubric-style prose ("The user would prefer responses that draw upon…"), not literal answers — ungradeable by any lexical bar. *Evidence:* 27 of 30 SSP golds in the 500-Q dataset are ≥40 words (only 3 are ≤38). *Counterweight:* the questions are official LongMemEval benchmark items; the golds are rubric-style **by design** (the official SSP template judges against a rubric, `judge.py:141-147` — "The model does not need to reflect all the points in the rubric"). Rewording them breaks comparability with published LongMemEval numbers; the official semantic judge handles them as-is.

**(c) The real problem is reporting methodology — exclusion + comparability note is the fix.** The issue's own target allows "re-scored methodology," and a note costs nothing. *Evidence:* benchmark scores depend on harness/protocol; incomparable surfaces shouldn't be blended. *Counterweight:* exclusion hides a real measurement defect and leaves the class ungradeable for every future long-gold question (the class is ~27/30 SSP questions, not 3); the comparability note is a **required accompaniment**, not a substitute for a working judge.

**(d) The real problem is LLM-judge cost/consistency vs deterministic bars.** LLM judges bring position/verbosity/self-preference bias, prompt sensitivity, key/cost dependencies. *Evidence:* external research (below). *Counterweight:* the semantic path here is **3 questions** at gpt-4o temperature 0 (≈$0.001/verdict, negligible); the repo already runs this exact judge for every graded eval question; containment is preserved everywhere else. Risk is real but mitigable (kappa validation, temp 0), so it constrains the design — it does not change the decision.

**Chosen problem frame (converged):** the *spot-check/CI judge surfaces* are the defect — they re-implement weaker lexical bars where the benchmark-authoritative semantic judge already exists in-repo and is unused there.

---

### Assumptions (with [validated]/[unverified])

- [validated] The word-overlap bar is structurally unreachable for the 3 questions: the bar is `max(2, len(set(gold.split()))//2)` — UNIQUE words — so d6233ab6 (56 unique) demands ≥28 exact-word overlap, 1d4e3b97 (47 unique) ≥23, b0479f84 (49 unique) ≥24. A correct paraphrase shares far fewer exact words (measured: a plausible paraphrase of d6233ab6 overlaps 9/28 — far below the bar). **Verifier-corrected arithmetic: NOT 39/34/31 (that would be total-word//2, not unique//2).** The runbook's "~45-word overlap" figure is also imprecise and must not be propagated.
- [validated] Measured-vs-structural distinction: the runbook's containment-judge-bar row names **d6233ab6 and 1d4e3b97**; **b0479f84's recorded failure is a reader-MODEL content error** ("commits wrong recs" — the judge was CORRECT to fail a wrong answer), and 1d4e3b97 is double-classified (retrieval-gap AND judge-bar). So a cleanly-measured "correct-but-paraphrased answer scored wrong" exists for **d6233ab6 only**; the other two are **structurally ungradeable by arithmetic** (no correct SSP answer clears the bar) but their recorded False verdicts are confounded by the #2069/#2070 classes. The plan's I1 live-probe is therefore a genuinely NEW measurement for 2 of the 3 questions, not a confirmation.
- [validated] MockJudge's containment rule is even stricter: the ENTIRE 68–79-word gold must appear as a substring of the answer (`grade_label`, `judge.py:232-239`) — a correct paraphrase can never contain a rubric-gold verbatim.
- [validated] The 3 long-gold questions are **d6233ab6 (79w), 1d4e3b97 (68w), b0479f84 (63w)** — verified against the live spot-check composition `/tmp/ask_spotcheck.json` (21 questions) and the cached dataset (`~/.cache/tortoise-longmemeval/longmemeval_s_cleaned.json`, 500 Q). b0479f84 is "the third."
- [validated] The measurement class is wider than 3: 27/30 SSP golds in the 500-Q dataset are ≥40 words; every SSP rubric-gold is ungradeable by lexical bars. The 3 are the ones that failed in the 21-question spot-check composition.
- [validated] The official judge is semantic and in-repo: `LLMJudge` + `OfficialJudgeModel` (gpt-4o-2024-08-06, temp 0, max_tokens 10, official anscheck templates verbatim). External sources confirm LongMemEval's official evaluation uses GPT-4o as an LLM judge with >97% agreement with human experts.
- [validated] The eval's GRADED runs already use the semantic LLM judge (`build_judge` → `LLMJudge`); the containment/word-overlap bars are used ONLY by (1) the QA spot-check (`tools/ask_spotcheck.py::_grade`) and (2) MockJudge-based CI/mock runs. The published benchmark numbers were never produced by the broken bar.
- [validated] Embedding infrastructure exists: `tortoise/embeddings.py` — bge-small-en-v1.5 (384-dim), `compute_embedding()`, `cosine_similarity_matrix()`, calibrated thresholds `NEAR_DUPLICATE_THRESHOLD=0.89` / `DEFAULT_THRESHOLD=0.72` (cross-vocabulary paraphrase band) from `tests/fixtures/labeled_pairs.jsonl`.
- [validated] The fix is measurement-layer only: no product-surface, graph, or ontology changes. No Tortoise graph writes are required (no `how-to-use-tortoise` invocation in scope).
- [validated] MockJudge-based CI fixtures (e.g. `test_reader_abstention_calibration.py`) use SHORT verbatim-matchable golds — unaffected by the fix (the reunion fixture's gold is 17-18 words, below any 30-word gate, matched by a near-verbatim fake).
- [validated] Judge-rubric fingerprint wiring exists: `JUDGE_RUBRIC_ID = "longmemeval-official"` (`run.py:4221`) → `judge_rubric_id_hash` in the run fingerprint (`run.py:1289`, `:4173`) — any eval judge-rubric change MUST bump it or stale checkpoints refuse via `CheckpointStaleError`. The spot-check is a separate surface (no fingerprint), so its judge change is recorded in its own output + the runbook.
- [unverified] Live gpt-4o verdicts on the 3 questions: whether the official judge credits a correct paraphrased answer to each (highly likely given the semantic rubric template + the >97% human-agreement claim, but not measured on THIS lane). Requires the key-gated real-model probe in the plan.
- [unverified] Whether an embedding-similarity threshold can separate correct-paraphrase from wrong-answer for these questions (rubric-vs-answer similarity is a different distribution than the labeled paraphrase pairs the 0.72 threshold was calibrated on; needs a calibration measurement).
- [unverified] The spot-check composition generator is NOT committed (composition lives only at `/tmp/ask_spotcheck.json`) — reproducibility gap; the plan commits a fixture.
- [unverified] Runtime-key impact: the semantic path on the spot-check lane needs a judge provider key (OPENAI_API_KEY for the official gpt-4o; the lane currently requires only DEEPSEEK/OPENROUTER/VENICE for the reader) — a new documented prerequisite.

---

### Adversarial Queries + Pre-mortem

**Adversarial web queries (disconfirmation-seeking) — run 2026-08-31:**

1. *"LLM-as-judge vs lexical overlap evaluation pitfalls long-form answers"* → LLM judges suffer prompt sensitivity, position/verbosity/self-enhancement bias, noisy edge cases (arXiv 2412.05579 survey; W&B "Exploring LLM-as-a-Judge"); Cohen's kappa + qualitative error analysis recommended over raw agreement (arXiv 2406.12624); overlap-style metrics are weak for open-ended tasks with many valid answers (arXiv 2410.20266); BLEU/ROUGE fail on paraphrased long-form answers (DigitalOcean).
2. *"RAGAS answer correctness metric long answers"* → RAGAS answer correctness is LLM-based: claim-level factual correctness (LLM breaks response+reference into claims, NLI overlap) + semantic similarity, weighted (docs.ragas.io; arXiv 2407.12873). Industry norm for long-form is LLM-based claim/semantic scoring, not lexical bars.
3. *"word overlap metric failure long-form generation evaluation paraphrase BERTScore"* → term-overlap metrics fail to capture semantic/stylistic similarity in personalized long-form generation (arXiv 2501.14956); BERTScore tolerates paraphrase but still favors surface similarity (arXiv 2407.04969); no universal threshold exists for lexical/semantic scores (galileo.ai).
4. *"GPT-4-as-judge position bias MT-Bench JudgeLM"* → position bias is systematic even in strong models (arXiv 2406.07791); mirror-judging mitigation (judge twice with swapped order, count only double-agreements — NeurIPS 2023 "Judging LLM-as-a-Judge"); GPT-4 exhibits the highest self-preference bias (arXiv 2410.21819); swap-order disagreement as a judge-validation metric (arXiv 2408.13006).
5. *"embedding cosine similarity threshold paraphrase detection calibration"* → thresholds are model-specific with wide optimal ranges (0.08–0.76; "The Threshold Trap," clawrxiv 2604.01081); MPNet paraphrase detection optimal ≈0.671 (MDPI 2025); ~0.45 heuristic for newer models (s-anand.net) — a fixed cutoff cannot be assumed; the repo's 0.72 bge-small band is one calibrated data point, not transferable to rubric-vs-answer scoring.
6. *"benchmark re-scoring comparability methodology note"* → benchmark scores depend on model/harness/protocol; scores from incomparable harnesses must not be averaged (tensor.news methodology) — supports the required comparability note when the scoring method changes.

**Disconfirmation verdict:** the adversarial evidence does NOT disconfirm the defect (lexical bars provably fail on paraphrase), but it DOES disconfirm any naive "just use the LLM judge" or "just pick an embedding threshold" shortcut: the semantic path needs bias/consistency validation (temp 0, kappa), a documented key prerequisite, and a per-question scoring-method record. It also strengthens the hybrid (keep containment where it is precise) over wholesale replacement.

**Pre-mortem — "we shipped the semantic judge and long golds still scored wrong — why?"**

1. **The semantic judge over-credits.** gpt-4o answers "yes" to a rubric-satisfying but factually-wrong answer — the official SSP template is lenient by design ("The model does not need to reflect all the points in the rubric"), and verbosity bias rewards longer hedged answers. → Long golds score *too* fair, inflating the aggregate; nobody notices because the report doesn't record the scoring method or the judge call. **Guard:** record judge + verdict per question; kappa validation on a labeled sample; the correctness unit test pins a scripted wrong-answer → False.
2. **The length gate misfires (MOOT under the owner decision — full semantic).** `gold_words > threshold` classification was the hybrid's failure mode; under the approved full-semantic design there is no gate and no containment-vs-semantic split on the spot-check lane — the semantic judge grades EVERY question. The only remaining boundary is MockJudge-vs-semantic (CI vs live), governed by the consistency check. **Guard:** the CI MockJudge path keeps the deterministic bar; the consistency check records any divergence as a finding.
3. **The judge is inconsistent run-to-run.** Even gpt-4o at temp 0 has variance; without an agreement check, the 3 questions flip between correct/wrong across spot-check runs and the gate verdict is unreproducible. **Guard:** temp 0 locked (existing `OfficialJudgeModel.build_request`), double-judge on the 3 questions, agreement recorded in the spot-check output.

**Boundary check (OUT of scope):**
- Reader-model upgrades for the ask lane (#2069) — the runbook proves the reader MODEL is the binding constraint on the content failure class (deepseek-v4-flash cannot hold both derived-commit and near-miss-abstain; qwen3.8-max probe proves it). NOT this issue.
- Retrieval top-k/reranking for the ask lane (#2070) — the FTS top-40 miss class (ceb54acb rank ~70, etc.). NOT this issue.
- The (d) gate aggregate ≥0.8 — already MOOT by product decision (PR #2013: reader shipped, exposure gated); this fix does not unblock the gate and is not expected to (realistic verdict-count impact: 1 now — d6233ab6 cleanly flips on the judge fix alone; 1d4e3b97 needs #2070's retrieval fix too; b0479f84 fails on #2069's model class; the aggregate stays bound by model+retrieval classes).
- Detector parity (#2009) — separate tracked follow-up.
- Re-scoring ALL 27 long-gold SSP questions in the 500-Q dataset — the plan generalizes the mechanism (full semantic path on the spot-check lane, matching the graded eval) but the graded eval already uses the semantic judge; only the spot-check surface needs the wiring.
- Any Tortoise graph writes / ontology changes (docs/ONTOLOGY.md v3.6 untouched).
- Tiered budgets, streaming, multi-judge ensembling, LLM-as-judge for short golds.

---

### Problem-Converge Rationale + Falsification + Confidence

**Confirmed problem (one sentence):** The product-lane QA spot-check re-implements a weaker lexical bar (word-overlap `ask_spotcheck.py:88`; MockJudge substring containment `judge.py:371-381`) where the benchmark-standard semantic judge (official gpt-4o anscheck, `LLMJudge`) already grades every question on the graded eval lane — so the 3 SSP long-gold questions (d6233ab6 79w, 1d4e3b97 68w, b0479f84 63w), whose rubric-style golds no good answer reproduces lexically, are structurally ungradeable on the spot-check surface. **Owner decision 2026-08-31 (approved): the spot-check grades EVERY question with the semantic judge, benchmark-identical; the lexical bar is demoted to the key-free CI (MockJudge) substitute only.**

**Why this framing:** (1) the runbook measured the failure empirically (containment-judge-bar row); (2) the arithmetic is structural (≥½ gold-word overlap on a rubric-gold); (3) the benchmark-authoritative judge is semantic and in-repo — the defect is wiring, not capability; (4) indicator 2 (no short-gold regression) is satisfiable and strongest under the owner decision: the spot-check uses the SAME judge as the graded eval (benchmark-identical by construction), so "no regression" means spot-check verdicts agree with the benchmark's semantic verdicts — a consistency check, not a hybrid boundary; (5) the issue's own options list points here ("LLM-as-judge semantic scoring for long-gold questions"), and the owner extended it to all questions for benchmark parity.

**Rejected framings (with why):**
- *"The similarity metric is wrong for any length"* (a): correct in theory, but short-gold containment is precise in practice, deterministic, key-free, and indicator 2 mandates keeping it. Adopted as a long-gold-only correction.
- *"The questions are the problem — reword them"* (b): breaks official-benchmark comparability; the official rubric is semantic by design and handles them.
- *"Exclusion + comparability note is the fix"* (c): hides the defect, leaves the class ungradeable, contradicts indicator 1's "receive a fair score"; the note is a required accompaniment, not the fix.
- *"Cost/consistency kills LLM judges"* (d): 3 questions ≈ $0.003 total; the judge already runs on every graded question; consistency is controlled (temp 0, kappa) — a design constraint, not a blocker.

**Falsification check:** this framing is wrong if any of: (a) a correct-but-paraphrased answer to the 3 questions already scores True under containment/word-overlap on live answers — **falsified by arithmetic for all 3** (no correct SSP answer clears a ≥23-28/47-56-unique-word bar) and **falsified by the runbook measurement for d6233ab6** (correct content, failed bar); b0479f84's recorded False is a content-error verdict (judge correct), so its structural-unreachability claim is arithmetic-only until the I1 live probe measures it; (b) the official LLM judge scores the 3 questions wrong even when the reader answers correctly — **testable** via the plan's key-gated real-model probe (would force the fallback: exclusion or embedding judge); (c) the reader cannot produce a correct paraphrased answer to these questions at all — that is the #2069 model class, not the judge (would make the issue moot, not this framing wrong).

**Confidence: 85/100.** High on the defect, its mechanism, and the fix direction (all code-verified). 15 points off for: live gpt-4o verdicts on the 3 questions unmeasured on this lane; the no-regression boundary unmeasured until the agreement run executes; the uncommitted spot-check composition generator (reproducibility gap the plan closes).

---

### Axis Research (per axis, with citations + sources)

**Architecture axis — how the judge is built and wired.**
- *Codebase-first precedent scan:* `tools/longmem_eval/judge.py` — `LLMJudge`/`OfficialJudgeModel` (official gpt-4o anscheck, temp 0, max_tokens 10, no system message, `urllib` transport) is the semantic judge; `MockJudge` (containment + `_ABSTRACTION_MARKERS`) is the deterministic CI substitute; `NEAR_MISS_GRADING = "strict"` (#1949 decision record — the precedent for judge-rubric decision notes); `grade_label`/`classify_answer` (substring containment). `tools/ask_spotcheck.py::_grade` (word-overlap bar, lines 66-92). `tools/longmem_eval/run.py` judge call site 3507-3518; `JUDGE_RUBRIC_ID` (4221) + `judge_rubric_id_hash` fingerprint (1289, 4173); `report.py` methodology block (~696, `abstention_n` at 1664). `tortoise/embeddings.py` (bge-small, `compute_embedding`, `cosine_similarity_matrix`, calibrated 0.72/0.89). `battery/judge/gate.py` (KAPPA_MIN, position-bias AB/BA, IRT, stress — the repo's own LLM-judge validation discipline, from #1410).
- *External canonical + competitor precedent:* RAGAS answer-correctness = LLM-based weighted (factual-correctness claim-level NLI + semantic similarity) — the industry norm for long answers (docs.ragas.io/concepts/metrics/available_metrics/answer_correctness; arXiv 2407.12873). DeepEval = LLM-judge (G-Eval) with pytest-native CI regression gates (deepeval.com/blog/deepeval-vs-ragas; particula.tech/blog/deepeval-vs-ragas-vs-trulens-rag-evaluation-stack). LangSmith ships BOTH `correctness` (semantic similarity to reference via LLM-judge) AND `embedding_cosine_distance` as separate off-the-shelf evaluators — direct precedent for the hybrid (LLM semantic + deterministic) (docs.smith.langchain.com/reference/sdk_reference/langchain_evaluators; docs.langchain.com/langsmith/evaluation-concepts). TruLens = production tracing/observability (datasumi.com/blog/rag-evaluation-frameworks-comparison) — not applicable to a graded eval, noted for completeness.
- *Pitfalls:* LLM-judge position/verbosity/self-enhancement bias + prompt sensitivity (arXiv 2412.05579; wandb.ai/site/articles/exploring-llm-as-a-judge; arXiv 2410.20266); kappa over raw agreement (arXiv 2406.12624); BLEU/ROUGE failure on paraphrase (digitalocean.com/resources/articles/llm-as-a-judge).
- *Synthesis:* the architecture precedent (LangSmith's dual evaluators; RAGAS's LLM-based semantic scoring; the repo's own official judge) all point to: semantic LLM judge for long/paraphrase-tolerant scoring, deterministic for precise short-gold containment, with validation gates (kappa, position-swap) the repo already implements in `battery/judge/gate.py`.

**Research axis — evidence and methodology.**
- *Codebase-first:* `docs/runbook/1987-ask-abstention-check.md` (containment-judge-bar row: "the judge's `max(2, len(gold_words)//2)` word-overlap bar on ~70-90-word synthesis golds is structurally unreachable"; 0.38/0.43 spot-check aggregates; (d) MOOT by product decision); `docs/plans/2026-08-29-1987-ask-reader.md` (judge-stays-eval; Task 12 spot-check composition; the anscheck judge is the official rubric); `docs/research/2026-08-29-reader-answer-surface-competitors.md` (prior research artifact); judge.py #1949 decision record.
- *External:* LongMemEval official methodology = GPT-4o LLM-based judge with >97% agreement with human experts (emergentmind.com/topics/longmemeval; supermemory.ai/research/longmembench; mastra.ai/research/observational-memory); hybrid evaluation = GPT-4o judge for answer correctness + retrieval metrics (emergentmind.com/topics/longmemeval-benchmark); LongMemEval-V2 keeps the LLM-judge binary-label harness (arXiv 2605.12493); RAGAS factual correctness = claim-level NLI (docs.ragas.io).
- *Pitfalls:* term-overlap metrics fail on paraphrased long-form personalized generation (arXiv 2501.14956); token-overlap metrics (ROUGE, BERTScore) favor surface similarity and miss information-level equivalence (arXiv 2407.04969); benchmark scores depend on harness/protocol and incomparable surfaces must not be averaged (tensor.news/methodology) — the comparability-note requirement.
- *Synthesis:* the research evidence confirms (1) the defect is real and documented in-class, (2) the official judge is the authoritative semantic scorer, (3) any scoring-methodology change must be recorded (comparability note) — all three feed the plan.

**Ontology axis — graph/artifact implications.**
- *Codebase-first:* `docs/ONTOLOGY.md` v3.6 governs graph entities (Points/operators/edges, §1-§12); the judge is measurement-layer code in `tools/longmem_eval` + `tools/ask_spotcheck.py` — **no graph nodes, no operators, no edge types, no ontology change**. The relevant repo pattern is the *decision record*: `NEAR_MISS_GRADING = "strict"` (judge.py:65, #1949) and the #2027 calibration notes are code-adjacent decision records — the precedent for documenting "judge rubric/methodology change" next to the code. If the owner later wants the decision on the Tortoise graph, the `how-to-use-tortoise` skill governs it (out of scope here — the graph is not a required artifact of this fix).
- *External:* benchmark methodology governance — record the scoring change + comparability note so historical numbers stay interpretable (tensor.news/methodology; thrivesparrow benchmark-methodology docs on normalization across formats).
- *Synthesis:* the ontology axis contributes exactly one requirement: a decision-record note (like #1949) in `judge.py`/the runbook documenting the #2071 scoring change, its scope (spot-check/CI surfaces; graded eval untouched), and the comparability consequence for historical 0.38/0.43 aggregates.

---

### Integration Docs

**Dependencies + versions + API surfaces touched (existing in-repo, NO new third-party deps):**

| Dep/API | Location | Version/Shape | Used for |
|---|---|---|---|
| `build_judge(spec, mock)` → `Judge` | `tools/longmem_eval/judge.py:384+` | env `TORTOISE_LME_JUDGE_MODEL` (default `openai:gpt-4o-2024-08-06`); provider keys `OPENROUTER_API_KEY`/`DEEPSEEK_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY` | Semantic judge for long golds (Approach A/D) |
| `Judge.judge(*, question_type, question, answer, hypothesis, abstention) -> bool` | `judge.py:244` protocol | official anscheck templates; `LLMJudge` temp 0 max_tokens 10 | Replaces word-overlap for long golds |
| `MockJudge.judge` | `judge.py:371-381` | containment + `_ABSTRACTION_MARKERS` (12 + #2027 additions) | CI surface — add deterministic semantic variant for long-gold fixtures |
| `grade_label` / `classify_answer` | `judge.py:211-239` | normalized substring containment; `NEAR_MISS_GRADING="strict"` | Preserved verbatim for short golds (no-regression) |
| `tools/ask_spotcheck.py::_grade` | `ask_spotcheck.py:66-92` | word-overlap `max(2, len(gold_words)//2)` at :88 | PRIMARY fix site — length-gated router |
| `compute_embedding` / `cosine_similarity_matrix` | `tortoise/embeddings.py:164,214` | bge-small-en-v1.5, 384-dim; thresholds 0.72/0.89 | Option B (embedding judge) fallback — documented, not primary |
| `run_evaluation(reader, judge, …)` judge call | `tools/longmem_eval/run.py:3507-3518` | `_call_with_backoff` | Graded eval — UNTOUCHED (already semantic) |
| `JUDGE_RUBRIC_ID` / `judge_rubric_id_hash` | `run.py:4221 / 1289, 4173` | `"longmemeval-official"`, sha16 in fingerprint + report | Verified unchanged (eval rubric untouched); documentation-only |
| `report.py` methodology + `abstention_n` | `report.py:~696, 1664` | methodology block (judge_model, rubric hash) | Spot-check output records its own judge — report contract |
| `battery/judge/gate.py` (KAPPA_MIN, AB/BA position gate) | `battery/judge/gate.py` | #1410 discipline | Reuse validation methodology for the kappa/agreement check |
| `sentence-transformers` (optional extra) | `pyproject` `[embeddings]` extra | `>=3,<6` (probe docs) | Only if option B is pursued (already an eval runtime dep via hybrid retrieval) |
| Provider keys | env | DEEPSEEK/OPENROUTER/VENICE (reader, existing); **OPENAI_API_KEY (judge — NEW for the spot-check lane)** | Runtime prerequisite change, documented |

**No new libraries, no schema changes, no migrations, no graph writes.**

---

### Codebase Explorer Findings

**AFFECTED_FILES (paths + lines):**
- `tools/ask_spotcheck.py:62-92` — `_normalize` (:62) + `_grade` (:66): the containment fallback (:82-84) and the word-overlap bar (:88, `max(2, len(gold_words)//2)`). **PRIMARY fix site.**
- `tools/longmem_eval/judge.py:201-239` (`_normalize_answer_text`/`classify_answer`/`grade_label`), `:313-381` (`LLMJudge`/`MockJudge`), `:65` (`NEAR_MISS_GRADING`) — add a deterministic semantic variant for MockJudge; add the #2071 decision-record note; do NOT touch the official templates.
- `tools/longmem_eval/run.py:3507-3518` (judge call site), `:4221` (`JUDGE_RUBRIC_ID`), `:1289/:4173` (fingerprint) — READ-ONLY: verify no change needed (eval already semantic).
- `tools/longmem_eval/report.py:1664` (`abstention_n`), methodology block ~696 — READ-ONLY reference for the spot-check output contract.
- `tortoise/embeddings.py:38-39, 164, 214` — option-B infra (unchanged if B is not pursued).
- `tests/test_reader_abstention_calibration.py` (compliant-model fakes, short golds) + `tests/fixtures/` — the pattern + home for the new long-gold judge tests.
- **NEW (missing):** the 21-question spot-check composition generator — `/tmp/ask_spotcheck.json` is produced outside the repo (no committed generator found; only `ask_spotcheck.py:94` reads it). The 3 questions must be committed as a fixture as part of this work (reproducibility gap).

**PATTERNS_OBSERVED (existing judge/scoring patterns):**
1. **Official-rubric-verbatim discipline:** templates are copied verbatim from the benchmark (judge.py docstring) so published numbers stay comparable — any deviation is a documented decision record (#1949).
2. **Deterministic substitutes for offline lanes:** MockJudge (containment) + MockReader (evidence concat) give CI a key-free graded loop; the spot-check `_grade` is a third, weaker substitute.
3. **Compliant-model fakes for behavioral pins:** `test_reader_abstention_calibration.py` uses context-reading fakes that mechanically execute the pinned rule, so red→green legs verify END-TO-END WIRING offline. This is the pattern for testing the semantic judge offline.
4. **Key-gated real-model probes:** `test_real_model_commits_on_present_value` pattern — real-model verification only where the rule itself must be validated against a live LLM.
5. **Fingerprint + rubric-id gating:** any results-relevant methodology change rides the fingerprint (`judge_rubric_id_hash`) so stale resumes refuse.
6. **LLM-judge validation discipline:** `battery/judge/gate.py` (#1410) — kappa, AB/BA position bias, IRT, stress, drift re-validation.
7. **Calibrated embedding thresholds:** `tools/calibrate_thresholds` + `tests/fixtures/labeled_pairs.jsonl` — model-specific threshold calibration (bge-small 0.72/0.89).

**PARTIAL_IMPLEMENTATIONS:** the semantic LLM judge (`LLMJudge`/`OfficialJudgeModel`) is fully implemented but wired ONLY to the graded eval lane (`run.py` `build_judge`); the spot-check and MockJudge lanes re-implement weaker lexical bars instead of reusing it. No embedding-based answer checking exists anywhere (embeddings are retrieval/dedup-only). The `(d)` gate is MOOT by product decision, so the judge fix is follow-up (3) — measurement integrity, not gate unblocking.

**RECOMMENDED_TESTS:**
- Unit: length-gate classification (long/short boundary both sides, constant pinned); semantic path with a scripted fake judge (correct paraphrase → True; wrong/hedged answer → False; abstained-marker path for `_abs`); containment pins unchanged (existing `test_reader_abstention_calibration.py` + judge tests stay green); spot-check output records scoring method + judge model per question.
- Integration: spot-check on the 3 long-gold questions with the fake semantic judge (all True on correct paraphrases); short-gold no-regression agreement run (containment vs semantic → identical verdicts on the recorded sample).
- Contract: report/spot-check output records the scoring change + comparability note; runbook row updated; `JUDGE_RUBRIC_ID` verified untouched (eval surface unchanged); key-gated real-model probe (live gpt-4o on the 3 questions → fair verdicts on correct paraphrases).

**DEPENDENCIES:** stdlib only for the primary path (`urllib`, `json` in judge.py) + provider keys; `sentence-transformers`/`numpy` only if option B is pursued (already an eval runtime dep). No new deps, no migrations.

---

### Solution Approaches (diverge — 2-3 distinct)

**Approach A — Hybrid LLM-as-judge: containment for short golds, semantic for long golds (the issue's option a).**
- *Description:* Add a length gate (`gold_words > N`, N=30 default, env-tunable) to the spot-check `_grade`; long golds route to the existing `LLMJudge` via `build_judge` (official gpt-4o anscheck — benchmark-authoritative, semantic, >97% human agreement). Add a deterministic semantic variant to `MockJudge` (scripted rubric fake) so CI can pin long-gold fixtures offline. Short golds keep containment + word-overlap exactly as today. Spot-check output records per-question scoring method + judge model; runbook gets the comparability note.
- *Files:* `tools/ask_spotcheck.py`, `tools/longmem_eval/judge.py` (MockJudge variant + decision note), committed spot-check fixture (3 questions), `tests/` (unit + integration + key-gated probe), `docs/runbook/1987-ask-abstention-check.md`.
- *Architecture:* judge router in `_grade`; `build_judge(mock=False)` for the long-gold path; no changes to `run.py`/report.py eval wiring (already semantic).
- *Risks:* new OPENAI key prerequisite on the spot-check lane; LLM-judge bias/variance (mitigate: temp 0 locked, kappa/agreement check, double-judge the 3); comparability of historical aggregates (mitigate: methodology note).
- *Tradeoffs:* cost ≈ $0.001 × 3–30 questions (negligible); correctness = benchmark-authoritative; adds one key dependency to a QA lane that was deterministic.
- *Best-fit-if:* the goal is benchmark-comparable semantic fairness with minimal surface change and the OpenAI key is acceptable.

**Approach B — Embedding-similarity threshold judge (deterministic, key-free).**
- *Description:* For long golds, compute `cosine(compute_embedding(gold), compute_embedding(answer))` with bge-small and compare to a threshold calibrated for the answer-vs-rubric distribution (new calibration set from the 3 questions + negatives, following `tools/calibrate_thresholds` discipline). Deterministic, offline, CI-safe, no LLM key.
- *Files:* judge.py extension or a small new scorer module, calibration set + script, tests.
- *Architecture:* reuse `tortoise.embeddings` (`compute_embedding`, `cosine_similarity_matrix`); gate by gold length as in A.
- *Risks:* rubric-vs-answer similarity is a DIFFERENT distribution than the labeled paraphrase pairs the 0.72 threshold came from — a good SSP answer need NOT be textually similar to the rubric (it covers a subset; the official template says "does not need to reflect all the points"); mean-pooled 384-dim over 70-90-word texts is coarse; external evidence says thresholds are model-specific and fragile (optimal range 0.08–0.76 in the literature). Precision is UNPROVEN until a calibration measurement separates the classes.
- *Tradeoffs:* zero cost, zero new key, deterministic/reproducible — attractive; but precision risk is real and the calibration run may fail to separate.
- *Best-fit-if:* OpenAI keys cannot be used on the spot-check lane AND the calibration measurement separates cleanly; also the natural deterministic CI fallback if the LLM judge cannot be faked.

**Approach C — Rework/exclude the 3 questions + documented comparability note (the issue's option b).**
- *Description:* Exclude d6233ab6/1d4e3b97/b0479f84 from the spot-check composition (or reword their golds into short verbatim-matchable forms) and record a methodology note in the runbook + spot-check output explaining why and what historical numbers mean.
- *Files:* composition fixture (exclusion list), `tools/ask_spotcheck.py` (skip/reword handling), runbook.
- *Architecture:* none — composition-level change.
- *Risks:* hides a real measurement defect; the class stays ungradeable for every future long-gold question (27/30 SSP golds are ≥40 words); shrinks the 21-Q QA composition; indicator 1 ("receive a fair score") is only met in the letter ("re-scored methodology"), not the spirit — excluded questions receive NO score.
- *Tradeoffs:* cheapest, zero new infra/key; but leaves the measurement broken and generalizes to nothing.
- *Best-fit-if:* the semantic judge cannot be validated (no key, poor agreement) — the honest fallback that keeps the gate from lying.

**Approach D — Hybrid + precision measurement (rigorous A).**
- *Description:* Approach A PLUS a measured no-regression gate: run BOTH containment and the **REAL key-gated `build_judge()` judge** over the short-gold population (spot-check short-gold questions + a labeled sample) and assert identical verdicts with the divergence taxonomy applied (indicator 2 becomes a measured fact, not a claim; the scripted fake is a CI smoke only); the 3 long-gold questions get live-judge fair-verdict assertions (indicator 1). Report records scoring method per question + the agreement numbers + the comparability note.
- *Files:* A's files + the agreement-run harness/assertions.
- *Architecture:* A + a small agreement harness (containment vs semantic on the short-gold sample).
- *Risks:* A's risks + the agreement run must actually be run (if it reveals divergence on short golds, the semantic path is scoped even tighter — a finding, not a failure).
- *Tradeoffs:* slightly more work than A; directly satisfies both O/I/T indicators with evidence.
- *Best-fit-if:* the owner wants the no-regression claim verified, per QUALITY-OVER-CONVENIENCE.

---

### Solution-Converge Rationale + Rejected Alternatives

**PICK (owner decision 2026-08-31): full semantic judge on the spot-check lane — word-overlap/containment demoted to CI-only MockJudge substitute + precision measurement.**

**Rationale (QUALITY-OVER-CONVENIENCE):**
1. **Indicator 1 (fair long-gold scores) is met with the benchmark-authoritative tool.** The official gpt-4o anscheck judge is the scorer the benchmark itself uses (>97% human agreement, semantic rubric template), already implemented and tested in `judge.py`. **Under the owner decision the spot-check grades EVERY question with it — benchmark-identical, one grader to reason about, no hybrid boundary to explain.** No rubric change, no comparability break with published LongMemEval numbers.
2. **Indicator 2 (no short-gold regression) is preserved BY CONSTRUCTION and verified BY MEASUREMENT.** The spot-check now uses the SAME semantic judge as the graded eval — so short-gold verdicts on the spot-check agree with the benchmark's own semantic verdicts by construction. The consistency check (spot-check semantic vs graded-eval semantic over the shared short-gold population) measures that agreement; any divergence is a finding. The key-free CI path (MockJudge) keeps the deterministic bar for offline runs and is verified to not drift.
3. **It fixes the CLASS, not just the 3 questions.** Every rubric-gold/long-gold question becomes gradeable (27/30 SSP questions are ≥40 words) — future-proof, unlike exclusion.
4. **It closes a reproducibility gap found in exploration:** the 21-question composition is uncommitted; the plan commits the 3 questions as a fixture.
5. **The fallback ladder is documented:** if the live judge fails validation (kappa/agreement) or no judge key is acceptable → Approach C (exclusion + note); if determinism is mandatory → Approach B's embedding judge (pending a clean calibration).

**Rejected alternatives (with "when this WOULD have been better"):**
- **Approach B (embedding threshold):** rejected as the primary because rubric-vs-answer similarity is not the paraphrase-pair distribution its calibrated threshold serves, and external evidence shows embedding thresholds are model-specific and fragile; a failed calibration would burn the whole fix. *Would be better:* when the spot-check lane must stay fully deterministic/key-free and the calibration measurement separates correct-paraphrase from wrong at a stable threshold — and as the deterministic CI fixture grader if the LLM judge can't be scripted offline.
- **Approach C (exclusion + note):** rejected as the primary because it hides the defect and leaves the class ungradeable; the note is still REQUIRED as part of D. *Would be better:* if the semantic judge's kappa validation fails or no judge provider key is acceptable — exclusion + comparability note keeps the gate honest rather than flaky.
- **Approach A (hybrid without the agreement run):** rejected as the primary only because it would claim no-regression without measuring it — D costs one small harness and upgrades a claim into evidence. *Would be better:* if the owner accepts a documented (unmeasured) no-regression statement and wants the smallest possible diff.

---

### Plan Draft

**Problem statement:** The product-lane QA spot-check grades with a weak lexical bar (word-overlap `ask_spotcheck.py:88`; MockJudge substring containment `judge.py:371-381`) where the benchmark-standard semantic judge (`LLMJudge`, gpt-4o anscheck) already grades every question on the graded eval lane — so the 3 SSP long-gold questions (d6233ab6 79w, 1d4e3b97 68w, b0479f84 63w), whose rubric-style golds no good answer reproduces lexically, are structurally ungradeable on the spot-check. **Owner decision 2026-08-31 (approved): the spot-check grades EVERY question with the semantic judge (benchmark-identical); the lexical bar is demoted to the key-free CI (MockJudge) substitute only.**

**Proposed solution (approved):** full semantic judge on the spot-check lane + precision measurement (consistency vs the graded eval + CI parity) + full provenance (per-question scoring method, comparability note, decision record, committed fixture).

**Implementation steps (ordered):**
1. **Commit the spot-check fixture.** Add the 21-question composition (incl. d6233ab6/1d4e3b97/b0479f84 with their golds) as `tests/fixtures/ask_spotcheck_composition.json` (or a generator in `tools/`) so the gate is reproducible; update `tools/ask_spotcheck.py` to read it (path env-overridable, fallback to `/tmp` for compat).
2. **Replace the word-overlap bar with the semantic judge in `tools/ask_spotcheck.py::_grade` (owner-decision full-semantic).** `_grade` calls `build_judge()` (the official LLM judge) for EVERY question — short and long; the word-overlap bar (`ask_spotcheck.py:88`) is REMOVED from the product spot-check path. `abstention` handled by the existing `_abs` marker path (unchanged). **No length gate — there is no containment-vs-semantic split on the spot-check lane; benchmark-identical grading is the point.** Cost: 21 verdicts ≈ $0.02/run (negligible).
3. **Keep `MockJudge` as the deterministic CI substitute** (`judge.py`): containment + word-overlap + the `_ABSTRACTION_MARKERS` path stay as-is for OFFLINE/key-free CI runs (they pin wiring, not grading quality); add a deterministic semantic variant for long-gold CI fixtures (scripted rubric fake mirroring `test_reader_abstention_calibration.py`) so long-gold fixtures can be pinned offline. The CI surface is explicitly a substitute — the LIVE spot-check uses the semantic judge for everything.
4. **Record scoring provenance.** Spot-check output gains per-question `judge` (`llm` on the live path; `containment|word_overlap|mock_semantic` on the CI path) + `judge_model` fields; runbook row (follow-up 3) updated with the change + a comparability note: historical 0.38/0.43 aggregates graded the 3 questions under an unreachable bar and are not directly comparable; add the #2071 decision-record note to `judge.py`'s header (the #1949 pattern) recording the owner decision (full semantic on the spot-check, lexical bar demoted to CI).
5. **Precision measurement (the no-regression contract, owner-decision full-semantic).** Consistency harness: run the spot-check's 21 questions with the semantic judge AND compare verdicts against the graded eval's semantic judge on the shared population (they use the same `build_judge`/templates, so agreement is expected by construction — this measures it); plus a CI-parity check (MockJudge vs semantic over the short-gold population) recording any divergence as a finding. Divergence policy (required): (i) any divergence where the semantic judge over-credits a factually-wrong answer = BLOCKING finding; (ii) the temporal-reasoning off-by-one class (official template "do not penalize off-by-one errors for the number of days", judge.py:126-141; the 4 short-gold temporal questions e4e14d04/gpt4_7a0daae1/gpt4_6ed717ea/gpt4_8279ba02) is expected to differ from containment — that is the POINT of the owner decision (semantic is benchmark-correct; containment penalized it) = recorded finding, not a defect; (iii) marker-vocabulary `_abs` divergence = recorded finding (the 3 _abs questions grade on marker vocab; scoped as a documented class). **I2 acceptance = spot-check semantic verdicts agree with the graded eval's semantic verdicts on the shared short-gold population (0 unexpected flips; expected classes recorded + owner-signed).**
6. **Key-gated real-model probe + no-key runtime contract (verifier-fix).** Live `build_judge()` (gpt-4o) verdicts on the 3 questions with a **CURATED correct-paraphrase answer (human-verified-correct for each question — the probe injects the answer text directly to isolate the judge from reader capability; the current reader cannot produce a correct answer for b0479f84 (model class #2069) or 1d4e3b97 (retrieval class #2070))** → all True (indicator 1 at the top of the ladder). **No-key runtime behavior — DEFAULT: fail-fast pre-flight key check in `tools/ask_spotcheck.py` main()** (verify `OPENAI_API_KEY` (or the `TORTOISE_LME_JUDGE_MODEL` provider key) is set BEFORE any question is graded; if absent, exit with a clear message naming the prerequisite — NEVER a silent fallback to the unreachable word-overlap bar, which would re-False the 3 questions and produce a misleading aggregate). This is the named default; the fallback ladder (C exclusion / B embedding) remains an owner decision documented in the runbook, but the tool never runs a broken half-measure silently.
7. **Documentation:** `docs/runbook/1987-ask-abstention-check.md` (scoring-change + comparability note), `docs/ONTOLOGY.md` untouched (measurement-layer only), no graph writes.

**Testing strategy:**
- *Unit:* semantic path with the scripted fake (correct paraphrase → True; wrong/hedged → False; `_abs` marker path intact); the removed word-overlap bar has no remaining callers on the live path; containment/MockJudge pins unchanged (existing judge + calibration suites stay green — the CI-parity floor); spot-check output fields (judge, judge_model) present.
- *Integration:* spot-check over the 3 long-gold questions with the fake semantic judge → 3/3 True on correct paraphrases; live-path spot-check over the 21-question composition with the semantic judge → all questions graded, verdicts recorded per-question; consistency vs graded-eval semantic verdicts on the shared population.
- *Contract:* report/spot-check output records the scoring change + comparability note; runbook updated; `JUDGE_RUBRIC_ID`/fingerprint verified UNCHANGED (graded eval surface untouched); key-gated probe recorded.

**Acceptance criteria (mapped to O/I/T):**
- **I1 →** key-gated live judge returns True on a **curated, human-verified correct-but-paraphrased answer (injected directly, isolating the judge from reader capability)** for all 3 of d6233ab6/1d4e3b97/b0479f84 (and the fake-judge integration test pins the same offline).
- **I2 →** the consistency harness records **spot-check semantic verdicts agreeing with the graded eval's semantic verdicts on the shared short-gold population (0 unexpected flips)**; expected classes (temporal off-by-one — semantic is benchmark-correct where containment penalized it; marker-vocabulary `_abs`) are recorded as documented findings + owner-signed comparability note, NOT defects; any flip where the semantic judge over-credits a factually-wrong answer IS blocking; existing containment tests (CI path) stay green.
- **T1 →** 3/3 long-gold questions gradeable by the semantic judge (live probe + offline fake both green).
- **T2 →** 0 unexpected regression on short-gold verdicts (measured consistency vs the graded eval + unchanged CI pins).

**Runtime prerequisites:**
- `OPENAI_API_KEY` (or another `TORTOISE_LME_JUDGE_MODEL` provider key) — NEW and REQUIRED for the spot-check's live grading path (judge grades EVERY question now, ≈ $0.02/21-verdict run); `DEEPSEEK_API_KEY`/`OPENROUTER_API_KEY`/`VENICE_API_KEY` (reader, existing); dataset cache (existing); no new deps, no migrations, no graph writes.

---

### Wiring Check

| Touch point | Covered by | Status |
|---|---|---|
| Judge model API (`build_judge`/`LLMJudge`/`OfficialJudgeModel`, `TORTOISE_LME_JUDGE_MODEL` env, provider keys) | Step 2 (semantic judge for EVERY question), Step 3 (MockJudge CI substitute), Step 6 (key-gated live probe) | ✅ |
| Spot-check grading (`tools/ask_spotcheck.py::_grade`, word-overlap bar :88) | Step 2 (word-overlap REMOVED from the live path; `build_judge` grades every question) | ✅ |
| MockJudge/CI surface (`judge.py:371-381`, containment) | Step 3 (deterministic CI substitute kept; long-gold fixture fakes added; explicitly offline-only) | ✅ |
| Embedding infra (`tortoise/embeddings.py` `compute_embedding`, 0.72/0.89 thresholds) | NOT used in the primary plan (Approach B rejected); documented as the deterministic fallback if the LLM judge can't be validated | ⚠️ (optional path — no code until the fallback is chosen) |
| Report format / provenance (`report.py` methodology, `abstention_n:1664`; spot-check output) | Step 4 (per-question `judge`/`judge_model` fields + comparability note in runbook) | ✅ |
| Fixtures (the 3 questions; calibration fakes; composition) | Step 1 (commit composition fixture), Step 3 (long-gold fixture fakes), Step 5 (consistency sample) | ✅ |
| Cross-cutting: `JUDGE_RUBRIC_ID`/fingerprint (`run.py:4221,1289,4173`) | Verified unchanged — graded eval surface already semantic; no fingerprint bump needed (documented in Step 7) | ✅ |
| Cross-cutting: `NEAR_MISS_GRADING = "strict"` (#1949 decision record) | Unchanged (strict rubric intact for the eval); the #2071 decision-record note is ADDED alongside (Step 4) | ✅ |
| Cross-cutting: gate (d) MOOT (PR #2013) / separate failure classes (#2069 model, #2070 retrieval, #2009 detector) | Explicitly out of scope; boundary check documents the class separation | ✅ |
| Cross-cutting: cost/budget (semantic judge on the spot-check lane) | Bounded: 21 verdicts ≈ $0.02/run; documented prerequisite (judge key REQUIRED on the live path) | ✅ |
| Cross-cutting: uncommitted composition generator (reproducibility) | Step 1 closes the gap (fixture committed) | ✅ |

**Blocking gaps:** none. The judge-key prerequisite (accept `OPENAI_API_KEY` on the QA lane) is now REQUIRED by the approved owner decision (full semantic), not optional; the fallback ladder (C exclusion / B embedding) remains documented for key-free environments.
