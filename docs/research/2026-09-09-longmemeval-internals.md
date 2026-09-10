# LongMemEval Benchmark Internals — Grounded Research Brief

**Date:** 2026-09-09
**Purpose:** Ground our LongMemEval 500-Q ("split s") measured slice (38 multi-session reasoning + 12 single-session "Information Extraction" questions; 0.78 under retrieval-only baselines) in a verified understanding of the benchmark: categories, gold-evidence annotation, judge semantics, official baselines, the independent leaderboard, and category-difficulty structure.
**Method:** Web + primary-source research only. Claims below were verified against: the ICLR 2025 paper (arXiv:2410.10813v2), the official repo `xiaowu0162/LongMemEval` (incl. `src/evaluation/evaluate_qa.py` and `print_qa_metrics.py`), the released dataset `xiaowu0162/longmemeval-cleaned` (type counts computed directly from `longmemeval_oracle.json`, 500 entries), the Zep/Graphiti paper + launch blog (only published per-category GPT-4o full-context baselines we found), and several independent 2025–2026 system evaluations.
**Companion artifact in this repo:** `docs/benchmarks/comparison-systems.md` (memory-system comparison table).

---

## 1. Benchmark identity and taxonomy (categories + definitions)

**Paper:** Di Wu, Hongwei Wang, Wenhao Yu, Yuwei Zhang, Kai-Wei Chang, Dong Yu, "LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory," **ICLR 2025**. arXiv:2410.10813 (v1 Oct 2024, v2 Mar 2025). Code: `github.com/xiaowu0162/LongMemEval`. Data: `huggingface.co/datasets/xiaowu0162/longmemeval-cleaned` (`longmemeval_s_cleaned.json` ≈115k tokens/question ~40 sessions; `longmemeval_m_cleaned.json` ≈500 sessions/1.5M tokens; `longmemeval_oracle.json` = evidence-sessions-only oracle file). Dataset updated 2025-09 ("cleaned up the history sessions to prevent interference on answer correctness").

**Five core abilities** (paper §3.2), tested by **six ground `question_type`s** plus an abstention overlay:

| Ability | Definition (paper §3.2) | `question_type` (dataset) | Count in 500-Q (computed from oracle file) | Evidence sessions (non-abstention, computed) |
|---|---|---|---|---|
| **Information Extraction (IE)** — umbrella over three single-session types | "Recall specific information from extensive interactive histories, including details mentioned by either the user or the assistant." | `single-session-user` (task `single_hop`) | 70 (64 non-abs + 6 abs) | always exactly 1 |
| | | `single-session-assistant` (task `assistant_previnfo`) | 56 | always exactly 1 |
| | | `single-session-preference` (task `implicit_preference_v2`) | 30 | always exactly 1 |
| **Multi-Session Reasoning (MR)** | "Synthesize the information across multiple history sessions to answer complex questions that involve aggregation and comparison." | `multi-session` (tasks `two_hop` + `multi_session_synthesis`) | 133 (121 non-abs + 12 abs) | 2–5 (75@2, 24@3, 16@4, 6@5) |
| **Knowledge Updates (KU)** | "Recognize the changes in the user's personal information and update the knowledge of the user dynamically over time." | `knowledge-update` (task `knowledge_update`) | 78 (72 non-abs + 6 abs) | always 2 (old + updated) |
| **Temporal Reasoning (TR)** | "Awareness of the temporal aspects of user information, including both explicit time mentions and timestamp metadata in the interactions." | `temporal-reasoning` (tasks `temp_reasoning_implicit` + `temp_reasoning_explicit`) | 133 (127 non-abs + 6 abs) | 1–6 (20@1, 82@2, 15@3, 2@4, 5@5, 3@6) |
| **Abstention (ABS)** | "Identify questions seeking unknown information… and answer 'I don't know'." | any of the above whose `question_id` ends in `_abs` | 30 (6 user + 6 TR + 6 KU + 12 MR) | — |

Abstention questions are *false-premise* rewrites of real questions (answer field = an explanation such as "The information provided is not enough…"); the "answerable" total is 470, all judged, giving 500.

**Reading the ability/type relationship that matters for our slice:** "Information Extraction" is **not** a literal dataset label — it is the paper's umbrella ability over the three single-session types. The **IE-type questions the judge actually enforces most strictly are single-session-user and single-session-assistant** (extract a stated fact/entity/name), while single-session-preference tests *using* a stated preference to produce a personalized answer (rubric-graded; 30 Q). If your 12 "IE" questions are drawn from these three types, gold evidence is one specific session whose turns carry the answer.

---

## 2. Gold-evidence annotation design

- **Answer forms:** each instance's `answer` is "a short phrase… or a natural-language rubric describing the preferred answer in the case where [the question] is open-ended" (§3.1). Open-ended/rubric answers are used for `single-session-preference`; everything else is a concrete short answer.
- **Manual statement decomposition:** humans decompose each answer into one or more *evidence statements* with optional timestamps (curation pipeline §3.2). Evidence is deliberately **indirect**: an evidence session "convey[s] the evidence statement indirectly," e.g., a user reveals a purchase while asking about insurance — never "I bought a new car last month." Evidence sessions were human-screened so the statement is colloquially phrased and placed at varied positions.
- **Turn/session labels exposed in the data** (README): each evidence turn has `has_answer: true` (turn-level recall annotation); `answer_session_ids` lists the evidence sessions (session-level recall); `haystack_session_ids`/`haystack_dates` give the full history + timestamps. Sessions must be ingested **online** (one by one, in timestamp order) and the question arrives after the last session with its own `question_date`.
- **Gold is therefore a specific turn/span set, not a document**: turn-level `has_answer` labels exist precisely so memory systems can be scored on Recall@k/NDCG@k at both session and turn granularity (§3.3). For IE questions the gold is the single evidence session (and its labeled turns) inside ~40-session/115k-token haystacks.
- **Haystack construction:** needle-in-a-haystack style (§3.2): unrelated sessions sampled from simulated sessions + ShareGPT/UltraChat, evidence sessions inserted mid-stream with plausible timestamps; "coherent, extensible, and timestamped" so no conflict (realistic) is guaranteed minimal.

---

## 3. Judge semantics (official GPT-4o judge — read from `evaluate_qa.py`)

Model: **`gpt-4o-2024-08-06`**, temperature 0, `max_tokens: 10`, forced **binary yes/no**. There is **no exact-match scoring** and no rubric *scores* — each question gets one boolean. The paper reports **>97% agreement with human experts** (§3.3); per-category meta-eval Table 6: avg 0.98 (GPT-4o answers) / 0.97 (Llama-3.1-8B answers), weakest cells 0.90 for single-session-preference and abstention (open-ended judging).

**Per-type prompt semantics (verbatim structure):**

- **single-session-user / single-session-assistant / multi-session** — containment + equivalence: *"answer yes if the response contains the correct answer… If the response is equivalent to the correct answer or contains all the intermediate steps… you should also answer yes. If the response only contains a subset of the information required by the answer, answer no."* → **Extra info is NOT penalized** (containment, not exact-match); **partial aggregation is penalized** (matters for multi-part/multi-session answers); format is never penalized.
- **temporal-reasoning** — same, plus *"do not penalize off-by-one errors for the number of days"* (19 vs 18 days is correct). Date *semantics* must be right; date *arithmetic* tolerance is built in.
- **knowledge-update** — *"If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer."* → stale memory echoed alongside the update is forgiven.
- **single-session-preference** — rubric-graded but *leniently*: *"answer yes if the response satisfies the desired response… The model does not need to reflect all the points in the rubric. The response is correct **as long as it recalls and utilizes the user's personal information correctly**."* → **generic-but-plausible advice that does not ground in the retrieved user fact FAILS**; partial personalization can pass.
- **abstention** (`_abs` in `question_id` overrides all of the above) — *"answer yes if the model correctly identifies the question as unanswerable"* (may say info is incomplete, or that other info exists but the asked-for item doesn't).

**What the judge penalizes in practice (implications for our 0.78 slice):**
1. A reader that **hallucinates generic advice** on a preference/IE-preference question → judge sees no utilization of the user's stated fact → NO. (This matches your observed failure mode exactly.)
2. A reader that **abstains ("I don't know") on an answerable IE/MR question** → NO (containment fails); abstention prompts only rescue `_abs` questions. (Matches your observed false-abstention mode.)
3. MR answers that assemble only **part of** the required multi-session evidence → NO even if the included part is verbatim correct.
4. Not penalized: verbosity, reformulation, extra/irrelevant text, stale-info-plus-update (KU), off-by-one day counts (TR).

Aggregation per `print_qa_metrics.py`: per-type accuracy over the 6 types (each type's bucket includes its `_abs` items, which were judged with the abstention prompt), plus separate Abstention Accuracy (30 Q), Task-averaged Accuracy (mean of 6 type means), and Overall Accuracy (mean over all 500).

---

## 4. Official baselines (paper) and the published per-category full-context numbers

**Full-context vs oracle — GPT-4o, LongMemEval_S, overall 500-Q (paper Fig. 3b):**

| Setting | GPT-4o, no Chain-of-Note | GPT-4o + Chain-of-Note + JSON |
|---|---|---|
| **Oracle** (evidence sessions only) | **0.870** | **0.924** |
| **Full-context** (entire ~115k-token S history) | **0.606** (60.6%) | **0.640** (64.0%) |
| Drop | −30.3% | −30.7% |

- Other long-context LLMs, full-context S (no-CoN / +CoN): Llama-3.1-70B 0.334/0.286; Llama-3.1-8B 0.454/0.420; Phi-3-14B-128k 0.380/0.344; Phi-3.5-mini 0.342/0.324 (Fig. 3b; more LLMs in appendix Table 8, e.g., Qwen2.5-7B 0.128 full-context S). The paper's headline: long-context LLMs drop **30–60%** from oracle to full-context on S.
- **Commercial pilot (97-Q subset, 3–6 sessions, human-turn replay):** offline GPT-4o reading 0.9184; ChatGPT (GPT-4o) 0.5773, (GPT-4o-mini) 0.7113; Coze (GPT-4o) 0.3299, (3.5-turbo) 0.2474 (Fig. 3a). Coze's failure mode per paper: doesn't record *indirectly provided* user info; ChatGPT: overwrites crucial info.
- **The paper does NOT publish per-category full-context numbers.** The only per-category GPT-4o full-context table we found is Zep's independent rerun on LongMemEval_S (blog, Jan 2025) — widely treated as the per-category baseline:

**Zep's published per-category full-context baselines (gpt-4o):**

| Question type | Full-context GPT-4o | Full-context GPT-4o-mini |
|---|---|---|
| single-session-preference | **20.0%** | 30.0% |
| temporal-reasoning | **45.1%** | 36.5% |
| multi-session | **44.3%** | 40.6% |
| knowledge-update | **78.2%** | 76.9% |
| single-session-user | **81.4%** | 81.4% |
| single-session-assistant | **94.6%** | 81.8% |
| **Overall** | **60.2%** | 55.4% |

(Your "~60.2 full-context" figure is exactly Zep's overall GPT-4o rerun; the paper's own figure is 60.6% — same ballpark; the small delta is dataset-cleaning + harness differences. **Overall ordering = category difficulty for a full-context reader:** preference ≪ MR ≈ TR ≪ KU ≈ user ≪ assistant.)

- Paper's own optimized memory design (their 3-stage index/retrieve/read framework; all QA results on **LongMemEval_M**, GPT-4o reader): value = round decomposition beats whole-session; key = value+extracted-user-fact expansion adds +9.4 Recall@k / +5.4 accuracy (Table 3); time-aware query expansion adds 6.8–11.3 Recall@k on TR (Table 4); best full design (rounds + fact keys + time-aware + CoN/JSON reading) reaches **0.720 (Top-10) / 0.657 (Top-5)** QA (§5). Under **oracle retrieval**, reading strategy alone moves GPT-4o up to **10 absolute points** (§5.5); E.5 error analysis: correct retrieval is necessary for ~90% of correct answers, yet **40–50% of errors are "correct retrieval, wrong generation"** — higher for weaker readers.

---

## 5. Independent leaderboard numbers

**Caution:** scores below are **not apples-to-apples** — they differ in dataset version (S vs cleaned), retrieval top-k / context budget (≈1.6k tokens for Zep vs up to hundreds of retrieved memories for mem0's harness), answerer model (GPT-4o → Gemini 3 Pro / GPT-5-class), and judge. The official paper judge and protocol are the only standardized axis.

**Systems that beat the GPT-4o full-context baseline (60.2–60.6%):**

| System | LongMemEval score | Notes / source |
|---|---|---|
| **Zep / Graphiti** | **71.2% (gpt-4o)**, 63.8% (gpt-4o-mini) | Temporal knowledge graph (Graphiti), bi-temporal model; +18.5% aggregate over full-context, ~90% lower latency, ~1.6k context tokens vs 115k. Per-category: +184% on preference (20→56.7), +38% TR, +31% MR, +14% user, +6.5% KU; **below baseline only on single-session-assistant** (94.6→80.4, −17.7%). [Zep blog; Zep paper arXiv:2501.13956] |
| **AutoMem** | 74.4% (official rerun, longmemeval/s n=500, ±3.8) | Recalls context ~3.8k tokens; **Recall@5 = 97.0%** — its own analysis: remaining misses are "synthesis or representation work," not retrieval. [automem.ai/benchmarks] |
| **SupeMemory** | 81.6% (gpt-4o) | Self-reported; agent-swarm/LLM search over compressed memory; claims ~99% with their best agentic setup. [automem.ai table; aihola.com coverage] |
| **Mastra Observational Memory** | 84.2% (gpt-4o) / 94.9% (gpt-5-mini) | Self-reported. [mastra.ai/research/observational-memory; automem.ai table] |
| **Honcho** | 90.4% (self-reported); 92.6% (Gemini 3 Pro) | Self-reported. [automem.ai table] |
| **Hindsight (AMB/vectorize)** | 91.4–94.6% | Neutral-harness runs (AMB) with frontier answerers/judges; structured-memory architecture. [automem.ai; agentmemorybenchmark.ai] |
| **HydraDB** | 90.2% (Gemini 3 Pro) | Self-reported. [automem.ai table] |
| **Mem0** | **~49.0%** (independent/older; attributed to a Vectorize-run evaluation) vs **94.4% (472/500)** self-published (cloud, GPT-4o answerer+judge; per-type: user 98.6, assistant 98.2, preference 96.7, TR 97.0, KU 93.6, **multi-session 88.0**) | The 49.0 figure is the one third-party blogs cite as "Mem0"; mem0's own harness feeds large top-k (10/20/50/200) of retrieved memories, inflating scores vs Zep's ~1.6k-token budget. [digitalapplied.com citing Vectorize; mem0.ai/memory-benchmarks GitHub + docs] |
| **Letta / MemGPT** | Not published | Explicit open request for LongMemEval coverage; DMR (their own benchmark) is where they publish. [automem.ai; mem0 2026 report] |
| **Paper pilot (commercial)** | ChatGPT 57.7%, Coze 33.0% (97-Q, 3–6 sessions) | Human-turn online replay, far easier than S. [paper Fig. 3a] |

Independent mid-2026 aggregator snapshot (self-reported, not re-run): AutoMem 74.4, SupeMemory 81.6, Honcho 90.4/92.6, Hindsight 91.4–94.6, HydraDB 90.2, Mastra 84.2–94.9, mem0 (cloud) 94.4, Letta n/p. **[automem.ai/benchmarks]**

---

## 6. Category-difficulty analysis & why retrieval systems underperform (or beat) full-context

**Difficulty ordering (full-context GPT-4o, per Zep's published rerun, low = hard):**
`single-session-preference` (20%) ≪ `multi-session` (44%) ≈ `temporal-reasoning` (45%) ≪ `knowledge-update` (78%) ≈ `single-session-user` (81%) ≪ `single-session-assistant` (95%).

**Why each hard category is hard for full-context reading:**
- **Preference:** the gold preference is *revealed indirectly* and must be recalled from one turn among 115k tokens, then *applied* — the model that fails defaults to generic advice, which the rubric judge rejects (it checks "recalls and utilizes the user's personal information"). Open-ended judging also makes it the least reliable cell of judge meta-eval (0.90).
- **Multi-session reasoning:** needs aggregation/comparison across 2–5 evidence sessions scattered in ~40 sessions; the judge demands the *complete* synthesized answer ("only a subset… answer no"), so a single missed evidence session fails the whole question.
- **Temporal reasoning:** requires aligning explicit time mentions + session timestamps + `question_date`; full-context models lose the timestamp association and mis-order events (Zep's published example shows gpt-4o-mini ordering three events wrongly even with the full transcript).
- **KU and single-session IE are "easier"** because evidence is 1–2 sessions, single-fact, and verbatim recoverable; assistant-uttered facts are easiest because the answer is stated in an assistant turn with little paraphrase noise.

**Why RAG/memory systems underperform full-context (when they do) — and the one case they beat it:**
1. **Full-context's loss is mostly assembly noise, not knowledge.** Paper: GPT-4o on evidence-only oracle = 87.0–92.4%, but with the same 500 questions inside 115k tokens = 60.6–64.0% (−27 to −31pp). This is the classic lost-in-the-middle / distractibility effect (paper cites Liu et al. 2024, Shi et al. 2023). So a memory system's ceiling is "approach oracle": feed only relevant, timestamp-ordered evidence.
2. **Retrieval is necessary but not sufficient.** Paper E.5: with its best pipeline, ~90% of correct answers required correct retrieval, yet 40–50% of *errors* had correct retrieval and wrong generation (worse for weak readers). AutoMem's own run shows the same saturation at scale: **Recall@5 = 97% while QA accuracy is 74%** — additional recall cannot buy accuracy once retrieval is adequate. This is the direct structural explanation for your observation that "improving retrieval recall does not lift accuracy": in the hard tail, remaining headroom is in (a) evidence *completeness and ordering* for MR, and (b) reader-side synthesis/personalization — not in finding more evidence.
3. **When a system beats full-context, the architecture change is evidence assembly, not retrieval.** Zep/Graphiti wins by converting 115k tokens into ~1.6k tokens of temporally-annotated, deduplicated, entity-linked facts (near-oracle density + time-awareness). The negative cell proves the mechanism: assistant-uttered verbatim facts are compressed away, so Zep *drops below* full-context on single-session-assistant (94.6→80.4). Systems that ingest verbatim turns (rounds as values, high top-k, e.g., the paper's own RAG and high-top-k harnesses) protect IE recall but pay in context noise on synthesis-heavy categories.
4. **Reader capability interacts with evidence density** (paper §5.2): weak readers degrade past ~3k retrieved tokens, while GPT-4o keeps improving past 20k — so "feed more evidence" only works if the reader is strong; CoN + structured JSON prompting recovers up to 10 absolute points under oracle retrieval (§5.5).

---

## 7. Synthesis — what the benchmark actually rewards

LongMemEval 500-Q is a **three-stage pipeline benchmark** (index → retrieve → read) whose scoring function makes the *third* stage the final arbiter:

1. **Judge semantics decouple "found it" from "said it right."** The official judge is containment/semantic-equivalence per type — never exact match, never format-scored, tolerant of extra text, stale-info-plus-update (KU), and off-by-one days (TR). What it *does* punish: partial multi-session syntheses, generic (non-user-grounded) advice on preference questions, and any abstention on answerable questions.
2. **Retrieval quality matters only up to the ~90% necessity threshold.** Correct retrieval is a precondition for ~90% of correct answers, but once recall saturates (the paper's E.5; AutoMem's 97% Recall@5 with 74% accuracy), benchmark headroom is entirely in **evidence assembly and reading**.
3. **What separates systems that beat full-context (60.2%) is evidence assembly that mimics the oracle file**: complete, timestamp-ordered, verbatim-where-it-matters evidence at high density (Zep ~1.6k tokens; oracle = only evidence sessions), plus a strong reader with structured/CoN prompting. The mirror-image failure (Zep's −17.7% on assistant-said facts) shows verbatim fidelity is the axis that trades against density.
4. **For our measured slice (38 MR + 12 IE at 0.78):** at 0.78 we are above the full-context GPT-4o baseline for these categories (~44% MR / 20–81% IE) but far below the oracle ceiling (87–92%). Per the benchmark's own error anatomy, the plateau at ~0.78 with rising recall is the predicted signature that **remaining errors are reader-side**: incomplete MR synthesis (judge: subset → no) and preference/IE answers that don't ground in retrieved facts or abstain (judge: no). The levers the benchmark itself validates are (a) preserving evidence completeness + ordering/verbatim fidelity for MR, and (b) reader-side interventions — instructing the reader to cite the retrieved turn before answering, Chain-of-Note, and forbidding abstention unless the question is a true `_abs` false-premise item.

---

## Citations

1. LongMemEval paper (ICLR 2025): Wu, Wang, Yu, Zhang, Chang, Yu — arXiv:2410.10813v2, https://arxiv.org/abs/2410.10813 (HTML: https://arxiv.org/html/2410.10813v2)
2. Official repo + README + judge/metrics code: https://github.com/xiaowu0162/LongMemEval · judge code https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/evaluate_qa.py · aggregation https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/print_qa_metrics.py
3. Released data (cleaned, 2025-09): https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned (type counts in this brief computed directly from `longmemeval_oracle.json`, n=500)
4. Project page: https://xiaowu0162.github.io/long-mem-eval/
5. Zep blog — per-category full-context + Zep scores: https://blog.getzep.com/state-of-the-art-agent-memory/ (Jan 22, 2025)
6. Zep paper (Graphiti temporal KG): arXiv:2501.13956, https://arxiv.org/abs/2501.13956
7. mem0 self-published benchmark harness + results: https://github.com/mem0ai/memory-benchmarks (README: ingest→search→evaluate; answerer+judge default gpt-4o; top-k cutoffs 10/20/50/200) and https://docs.mem0.ai/core-concepts/memory-evaluation
8. mem0 ~49 independent figure attribution: https://www.digitalapplied.com/blog/open-source-agent-memory-mem0-letta-zep-compared (citing a Vectorize-run evaluation); secondary corroboration of "Zep 63.8 vs mem0 49.0": https://baeseokjae.github.io/posts/mem0-vs-zep-production-guide-2026/ , https://atlan.com/know/zep-vs-mem0/
9. Independent aggregator (mid-2026, self-reported rows + AutoMem official rerun): https://automem.ai/benchmarks/
10. Related: Letta benchmark-status issue (no LongMemEval score published), https://github.com/letta-ai/letta/issues (benchmark coverage request); Mastra Observational Memory write-up, https://mastra.ai/research/observational-memory

*Context note: several 2026 aggregator rows (Honcho, Hindsight, HydraDB, Mastra) are self-reported on non-uniform harnesses with frontier answerers/judges; treat as directional only. The only protocol-consistent numbers are the paper's own baselines (Fig. 3, Tables 3–8, E.1/E.5) and any run using the official `evaluate_qa.py` judge on the official data.*
