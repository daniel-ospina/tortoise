# #12297 — does the cross-encoder rerank earn its latency?

**Lever C1 of #12186.** The reranker was already BUILT (`tortoise/rerank.py`,
`CrossEncoderScorer`, landed under #317/#1545) and **not wired**. The wiring
landed on this branch (`31cf88be51`) behind a gate that ships **OFF**
(`TORTOISE_FUSED_RERANK`); this document is the calibration that decides whether
it earns that gate. Machine-readable artifacts sit beside it:
`measurement-noun-path.json` and `measurement-user-search.json` (the second was
NOT in this branch when §5 first cited it — §5 records that correction and then
carries the run that has since been performed).

Every number below was produced on **this box, on CPU** — it has no GPU — from
the **shipped** code path, not a re-implementation.

---

## 0. The gating question, answered first

> Does a cross-encoder trained on **passage** retrieval transfer to **short
> entity names**? Our nouns are **62.3 % bare definite descriptions**, not
> passages.

**RUNG TAKEN: 2.** Rung 1 (`mcp__seo-intelligence__perplexity_search`) was
attempted first and is **not available on this box** — `mcp_catalog` lists
`exa`, `brave-search`, `playwright-browser`, `gemini`, `tortoise`,
`search-console`, and **no `seo-intelligence` server at all**, so the tool cannot
load. Fell through to `web_search` with `model="sonar"` named explicitly.
`sonar-deep-research` / `sonar-reasoning-pro` were never used.

### What the primary sources actually say

| Source | Finding | Bearing here |
|---|---|---|
| [Model card — `cross-encoder/ms-marco-MiniLM-L6-v2`](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2) | "This model was trained on the **MS Marco Passage Ranking** task"; the published examples pair a **natural-language question** ("How many people live in Berlin?") with a **~20-token passage**; TREC-DL19 nDCG@10 **74.30**, MS MARCO dev MRR@10 **39.01** | Documents the training distribution — **question → passage**. It says nothing about noun-phrase → noun-phrase, so it neither licenses nor forbids transfer to our shape. |
| [BEIR — Thakur et al., NeurIPS 2021](https://datasets-benchmarks-proceedings.neurips.cc/paper/2021/file/65b9eea6e1cc6bb9f0cd2a47751a186f-Paper-round2.pdf) | In-domain scores are **not a reliable indicator** of out-of-domain behaviour; rerankers are the strongest zero-shot family but they degrade | The 74.30 above is **not** a predictor for our corpus. Transfer must be measured, not assumed. |
| [In Defense of Cross-Encoders for Zero-Shot Retrieval — arXiv 2212.06121](https://arxiv.org/abs/2212.06121) | Cross-encoders "largely outperform bi-encoders of similar size"; gains from scale are "marginal in-domain, but **much larger in new domains**"; early query–document interaction drives generalisation | Transfer is **expected in direction** (a true match still outranks an unrelated one) and **unknown in magnitude**. |

**Verdict: PLAUSIBLE, NOT DOCUMENTED — so measure.** The primary sources
establish that a cross-encoder of this family transfers out-of-domain *better
than its alternatives*, and they establish that the size of that transfer is
**domain-specific and must be measured**. Neither names our shape (a 2–4 word
bare definite description as the *query*, a 2–4 word entity name as the
*document*). Parking the lever on this evidence would have been a guess dressed
as a finding; the numbers below are the answer.

⚠️ **And the answer is narrower than "it transfers"** — §4 is the part that
must be read before anyone quotes §3.

---

## 1. Contradiction test

**No recorded decision reaches the calibration itself.** The measurement changes
no default, widens no window and writes no stored data — it reads a pool and
reorders it.

The **wiring** it calibrates must respect, and does:

- **the KEEP ruling** (#11395 comment `6781921075`) — nothing is deleted; the
  reranker reorders candidates that retrieval already returned;
- **§12.2 / §12.2c** — the pool width is **not** changed by this work
  (`DEFAULT_FUSED_RERANK_POOL = 40`; production's `limit=3` cut is untouched);
- **V1**;
- **#11901's conclusion — do not widen the noun-matching window.** The rerank is
  measured *at* pool 15 and pool 40, and the shipped default stays **3**. Pool
  widths are **measured, never adopted**; a reranker only chooses among rows the
  legs already returned, so this lever cannot widen the window even in principle.

---

## 2. Method — the shipped configuration, not a second implementation

**Noun path** — `tools/candidate_recall_11901.py --rerank` (arm 4, added on this
branch; default OFF so the committed #11901 artifacts stay byte-identical).

- corpus: this box's own `~/.tortoise/session-events`, **18 files / 11,001
  lines**, harvested **111,059** definite + **10,820** ident + **4,226** path
  distinct names (`population_digest 0aa80dff278c4e58`);
- population **1,000**, stratified sample **300** = 180 definite / 90 ident / 30
  path (**60.0 % bare definite descriptions** in the sample, reflecting the
  audit's 62.3 % share of real `:Object` names);
- per mention, the **real fused read** at pool width P, scored by the **shipped**
  `rerank.CrossEncoderScorer` with **one `score(query, contents)` call**, then
  cut to **3** — the production per-query window `extractor_v2._fts_rows` passes;
- retrieval legs verified `fts + structural + vector` = **hybrid, 5,856 windows,
  0 keyword-only** — the run refuses a mixed-mode artifact;
- **paired** against the unreranked order of the *same* rows, so `gained`/`lost`
  are counterfactual on one pool and cannot be explained by a population
  difference.

Model `cross-encoder/ms-marco-MiniLM-L6-v2` (the shipped default), torch threads
**pinned to 1** and recorded — the unpinned default measured ~20× slower per
pair on this box under fleet load, so an unpinned figure would describe the
scheduler.

---

## 3. Noun path — results

Baseline in every row is the **same** rows, unreranked, at the same pool.

### Pool 40 — the shipped `DEFAULT_FUSED_RERANK_POOL`, i.e. the real pool size

| metric | before | after | Δ |
|---|---|---|---|
| **candidate recall@3** | 244/300 = **81.3 %** | 300/300 = **100.0 %** | **+18.7 pts** |
| **nDCG@10** | **0.7847** | **0.9865** | **+0.2018** |
| gained / lost | — | **+56 / −0** | zero regressions |
| pairs scored | — | 12,000 | |

### Pool 15 — Zep's candidate count, the width #11901 measured

| metric | before | after | Δ |
|---|---|---|---|
| **candidate recall@3** | 244/300 = **81.3 %** | 277/300 = **92.3 %** | **+11.0 pts** |
| **nDCG@10** | **0.7847** | **0.9135** | **+0.1288** |
| gained / lost | — | **+33 / −0** | zero regressions |
| pairs scored | — | 4,500 | |

⛔ **The pool-15 number is the ceiling, and it is the WIDTH that sets it.** Arm 1
measured **277/300 golds inside a 15-window** (`in_window@15`), so 92.3 % is the
*maximum recall@3 any reranker can reach at pool 15* — the other 23 names are
never returned by the legs at that width. The rerank landed on **277, not 276**.
It recovered **every single in-pool candidate that fusion had pushed past the
cut**.

> **The bound is width-dependent, and an earlier version of this paragraph said
> otherwise.** It cited `miss_past_k = 0` as the ceiling proof and called the
> `miss_absent_at_max_k` rows unreachable "at every pool width". Both are wrong in
> the same direction: `miss_past_k` at k=P is **0 by construction** (a response
> has at most P rows, so "its rank is past P" cannot occur) — it is a property of
> the counter, not a measurement of the legs; and the pool-40 arm reaches
> **300/300**, so those rows are reachable. The ceiling is `in_window(P)` and it
> moves with the width; the only width-independent non-ceiling is
> `miss_absent_at_max_k`, which no reranker can reach.

**Surface variants** (a capture saying an existing node a different, real way —
article-drop / hyphen↔space / basename; 426 pairs):

| pool | in-window@3 before | after | gained / lost |
|---|---|---|---|
| 15 | 406/426 = 95.3 % | 425/426 = **99.8 %** | +20 / **−1** |
| 40 | 406/426 = 95.3 % | 424/426 = **99.5 %** | +19 / **−1** |

The single `lost` is the honest counter-case: a rerank is a permutation of a
fixed pool, so it can only trade one row's rank for another's, and one variant
mention paid for the 20 that gained.

---

## 4. What these numbers DO and DO NOT show — read before quoting §3

**The mechanism is lexical near-identity, and the data says so.**

The arm's query is the mention (the stored entity name, for the `exact` class)
and each candidate's document text is its **name**. So the cross-encoder is asked
"does *the credential* match *the credential*" — and it answers correctly almost
every time. That is not a semantic judgement; it is token-overlap detection over
2–4-word strings. The **surface-variant** arm is the same story one step weaker:
its perturbations are character-level only (`the credential` → `credential`;
`agent-infra` → `agent infra`), so those candidates share nearly all their tokens
with the query too.

So the correct reading is:

- ✅ **REAL, PAIRED, AND COUNTERFACTUAL:** on this corpus, at the shipped pool,
  inserting the rerank ahead of the `limit=3` cut makes the correct existing
  entity appear in the top 3 for **100 %** of exact mentions and **99.5 %** of
  surface-variant mentions, with **zero** regressions on the exact arm. Fusion
  alone was leaving **56 of 300** golds below the cut.
- ✅ **IT DOES NOT WIDEN THE WINDOW**, and cannot: it reorders what the legs
  returned. The 23 names the legs never return at pool 15 are still never
  returned — `miss_absent_at_max_k` is untouched by construction.
- ⚠️ **IT IS NOT EVIDENCE THAT ms-marco TRANSFERS SEMANTICALLY TO ENTITY
  NAMES.** The task it is actually winning is *name matching*, which needs no
  domain knowledge and therefore transfers trivially. A pure token-overlap
  baseline would likely reproduce much of this gain — and that control was
  **not run here** (see §7).
- ⚠️ **IT SAYS NOTHING ABOUT USER SEARCH**, where the query is a passage span and
  the documents are captured turns — a genuinely different and harder task.
  That is §5, and it is where the model's actual training distribution lives.

---

## 5. User search — the second surface, and it is a DIFFERENT verdict on cost

> **Provenance, kept because the correction it records is real:** an earlier draft
> of this section cited `measurement-user-search.json` and "§5 of this document's
> results table" **before either existed** — a dangling citation in the one
> section whose job is to say whether a second surface was measured. That was
> corrected to *NOT MEASURED*, and it was true at the time. **The run has since
> been performed and its artifact is committed in this branch**, so the section
> below is the measurement the citation originally claimed. The fixer's rule still
> binds: a section may not cite a number it does not carry.

`tools/rerank_calibration_12297.py` (the fixed, DECLARED query set landed in
`65968f8a70`), run over **400 real captured turns** written to a run-scoped graph
(`rerank12297_1794530438_106980`), population digest `ba6699b97f026982`, the real
fused read through `tortoise_fts_query` at `limit=40` (the shipped
`DEFAULT_FUSED_RERANK_POOL`), the shipped `rerank_pool`, cut `[:10]`. Paired: the
before-order is the SAME pool's fused order.

**Declared caveat, restated because it bounds every number below:** the query is
built from its own gold passage (first 8 words / a mid 8-word window). It is a
harder-than-trivial but **easier-than-real** task — it measures promotion of the
correct passage above near-duplicates the legs already ranked ahead of it, and is
**not** an estimate of production search quality.

| arm | nDCG@10 before → after | recall@10 before → after | gained / lost | gold never in pool |
|---|---|---|---|---|
| **prefix** (first 8 words) | 0.1301 → **0.6766** | 26.5 % → **67.8 %** | **+165 / −0** | 129/400 |
| **midspan** (mid 8 words) | 0.1632 → **0.7516** | 34.0 % → **75.2 %** | **+165 / −0** | 99/400 |

**Both metrics improve on both arms, with zero regressions** — the falsifier does
not fire on this surface either. And the ceiling is visible in the table: the
`gold never in pool` column is the hard limit, because **a permutation cannot
recover a row retrieval never returned** (129/400 prefix, 99/400 midspan). Those
queries stay in the denominator at score 0 — dropping them would let a reranker
raise nDCG by losing queries.

### ⛔ The cost verdict on this surface is NEGATIVE, and it is the same measurement

User search scored **31,511 pairs in 2,685.6 s** — **85.2 ms/pair**, i.e.
**≈ 3.4 s added per query** at pool 40. That is ~16× the noun path's
5.179 ms/pair, and the cause is **passage length, not load or model**: the noun
path's candidate text is a 2–4-word **name**, while here it is a 120–900-char
**turn**. Measured directly on this box at loadavg 2.7, same model, same pinned
thread:

| candidate text | ms/pair |
|---|---|
| name-length (~4 words) | **7.5** |
| passage-length (~150 words) | **62.9** |

So the user-search rerank **improves ranking and is not affordable**: 3.4 s per
query on CPU, per query, in the interactive search path. **The lever earns its gate
on the noun path only.** This is a scoped recommendation, not a general one, and
the gate's default-off posture is what makes that scope enforceable.


---

## 6. Latency, at the real pool size, on CPU

Measured over **39,930 pairs** in the arm-4 run (torch intra-op threads pinned to
**1**). The count is the arm's own arithmetic, so it is checkable against §2/§3:
**300 mentions × (15 + 40) = 16,500** exact-arm pairs + **426 variants × (15 + 40)
= 23,430** variant pairs, recorded in `arms.rerank.latency.pairs_scored` beside
`score_s` and the per-pair mean — the three reconcile (`5.179 ms × 39,930 =
206.8 s`). It is the 300-mention sample, not the 1,000-name population.

| | |
|---|---|
| **ms per pair** | **5.179** |
| pairs per mention at pool 40 | 40 |
| **added latency per mention at pool 40** | **≈ 207 ms** |
| added latency per mention at pool 15 | ≈ 78 ms |
| model load (from local HF cache) | 6.4 s, once, cached by name across both gates — **an observation, not a reproducible figure: it is NOT in the artifact**, whose `latency.note` says load is excluded |

**Cost at the production shape:** a capture issues up to `max_queries=15` derived
object queries, so pool 40 costs **≈ 3.1 s added per capture** (pool 15 ≈ 1.2 s).
That is the price of the gate, and it is the reason the gate ships **OFF** and
opt-in rather than default-on.

This is a **CPU** figure. It is not comparable to the model card's
"1800 docs/sec" (computed on a **V100**). The comparable number is the measured
throughput: **1 / 0.005179 s ≈ 193 pairs/s**, so the V100 figure is **≈ 9×** this
box's, not thousands. (An earlier draft of this sentence said ~5,400×, which is
the arithmetic of 3 s/pair, not 5 ms/pair — the correction matters because the
ratio is the whole reason a reader might wrongly assume the CPU cost is
irreducible.)

⛔ **This figure is specific to SHORT candidate text.** It is cheap because a
noun-path candidate is a **name** (2–4 words). On user search the candidate is a
full turn and the same model costs **85.2 ms/pair → ≈ 3.4 s per query** (§5) —
measured, not extrapolated. Do not carry 5.179 ms/pair over to any surface whose
candidate text is a passage.

---

## 7. Verdict against the falsifier

> *If neither nDCG@10 nor candidate recall improves at the real pool size, park
> the lever with the numbers. A zero is a result.*

**Both improve, at the real pool size, with zero regressions on the exact arm:**

- candidate **recall@3: 81.3 % → 100.0 %** (pool 40, +56 gained / −0 lost)
- **nDCG@10: 0.7847 → 0.9865** (pool 40, +0.2018)

**→ The lever is NOT parked. It earns its gate, and the gate stays OFF by
default** — the rerank is worth ~207 ms/mention when a caller opts in, and the
default-off posture in `31cf88be51` is the right one given that cost.

**On user search the ranking also improves (§5: nDCG@10 0.1301→0.6766 and
0.1632→0.7516, recall@10 +41.3 / +41.2 pts, zero regressions) but the lever is
NOT affordable there: ≈ 3.4 s per query on CPU.** So the honest scope of this
result is: **keep the gate, ship it OFF, and treat the noun path — not user
search — as the surface it earns.**

### Residuals (stated, not hidden)

1. **No lexical-baseline control was run.** The decisive missing experiment is a
   trivial token-overlap / `difflib` scorer over the same pools. If it reproduces
   §3, then this lever buys name-matching that a 5 µs string compare already
   provides, and its 207 ms/mention is unjustified. **This is the first thing a
   follow-up should run** — it is one flag on the arm already on this branch.
2. **The exact arm's query is byte-identical to the gold's name** (it is the
   stored name), so its 100 % is an upper bound on that surface, not an estimate
   of production precision.
3. **Surface variants are character-level only.** No morphological, reordering,
   or synonym perturbation was measured, so "99.5 %" describes separator/case
   noise, not paraphrase.
4. **Single corpus, single run.** 300 mentions is a sample; the sample is
   stratified and the population digest is recorded, so a re-run is comparable,
   but there is no second corpus.
5. **`score()` is called once per mention over the whole pool** in this arm.
   Production calls it once per derived query too, so the latency maps directly —
   but the production caller also pays a batched content read, which is **not**
   included in the 5.179 ms/pair.

---

## Sources

- [Model card — `cross-encoder/ms-marco-MiniLM-L6-v2`](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2)
- [BEIR — Thakur et al., NeurIPS 2021](https://datasets-benchmarks-proceedings.neurips.cc/paper/2021/file/65b9eea6e1cc6bb9f0cd2a47751a186f-Paper-round2.pdf)
- [In Defense of Cross-Encoders for Zero-Shot Retrieval — arXiv 2212.06121](https://arxiv.org/abs/2212.06121)
- #11901 — the candidate-recall corpus and instrument this reuses
- #12186 — lever C1
