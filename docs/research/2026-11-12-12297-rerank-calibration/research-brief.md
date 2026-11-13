# #12297 — does the cross-encoder rerank earn its latency?

**Lever C1 of #12186.** The reranker was already BUILT (`tortoise/rerank.py`,
`CrossEncoderScorer`, landed under #317/#1545) and **not wired**. The wiring
landed on this branch (`31cf88be51`) behind a gate that ships **OFF**
(`TORTOISE_FUSED_RERANK`); this document is the calibration that decides whether
it earns that gate. Machine-readable artifacts sit beside it:
`measurement-noun-path.json`, `measurement-user-search.json` (the second was
NOT in this branch when §5 first cited it — §5 records that correction and then
carries the run that has since been performed) and `measurement-lexical-control.json`,
which carries every figure in §8 (the lexical arms' recall, nDCG and per-pair cost).

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

**Noun path** — `tools/candidate_recall_11901.py --rerank` (arm 5 — main's #12302 name-entropy
gate holds arm 4 — added on this branch; default OFF so the committed #11901 artifacts stay byte-identical).

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

Every figure in this section is `measurement-noun-path.json` (`arms.exact` /
`arms.variant` before, `arms.rerank.<pool>` after) — named here for the reason §5
gives: a figure may stand only if it is a field in a committed artifact named
beside it. Baseline in every row is the **same** rows, unreranked, at the same pool.

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
> `miss_absent_at_max_k` rows unreachable "at every pool width". Both are wrong, and
> in the same direction: `miss_past_k` at k=P is **0 by construction** (a response
> has at most P rows, so "its rank is past P" cannot occur) — a property of the
> counter, not a measurement of the legs; and the pool-40 arm of **this same run**
> reaches **300/300**, so those 23 rows are reachable at a modestly wider pool.
> `miss_absent_at_max_k` therefore means "absent within arm 1's **maximum** window
> (15)", **not** "absent at every width" — it is width-dependent too. The ceiling is
> `in_window(P)` and it moves with the width; this run measures no width-independent
> bound at all, which is the honest statement and the reason the widths are quoted
> as a pair.

**Surface variants** (a capture saying an existing node a different, real way —
article-drop / hyphen↔space / basename / hash-dropped, five character-level
perturbations summing to **426 pairs**: article-drop 180, space→hyphen 180,
basename-only 30, hyphen→space 18, hash-dropped 18 — `arms.variant_by_class`):

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
  returned. At **pool 15** the 23 names the legs never return stay unreturned —
  untouched by construction. ⛔ Scoped deliberately: at **pool 40** this same run
  returns all 23, so what is "unreachable" here is unreachable *at that width*, not
  in principle, and the width is the only thing that changes it.
- ⚠️ **IT IS NOT EVIDENCE THAT ms-marco TRANSFERS SEMANTICALLY TO ENTITY
  NAMES.** The task it is actually winning is *name matching*, which needs no
  domain knowledge and therefore transfers trivially. A pure token-overlap
  baseline would likely reproduce much of this gain — **and it does: that
  control was run, and §8 is its result** (identical EXACT-arm recall, equal-or-better
  nDCG, ~1,000× cheaper). So the semantic claim is bounded to nothing here, and
  this section's warning is now a measured finding rather than a suspicion.
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
5.179 ms/pair, and the mechanism is **passage length** — and, because the two runs are not load-matched (see below), load cannot be excluded as a contributor to the ratio: the noun
path's candidate text is a 2–4-word **name**, while here it is a 120–900-char
**turn**. Both quantities come from the TWO COMMITTED ARTIFACTS, so each number has
a run behind it and they are directly comparable in provenance if not in load:

| candidate text | ms/pair | artifact |
|---|---|---|
| name-length (~4 words) | **5.179** | `measurement-noun-path.json` → `arms.rerank.latency` |
| passage-length (~40–150 words: the harness admits 120–900 chars AND ≥40 words) | **85.227** | `measurement-user-search.json` → `latency` |

⛔ **The 16× gap is real; the absolute figures are not load-matched.** The user-search
run recorded `loadavg_1m = 14.0` and the noun-path run did not record one, so read
the RATIO as the finding and neither figure as a clean-box latency. (An earlier
draft of this section quoted 7.5 / 62.9 ms from a one-off micro-benchmark at loadavg
2.7 that was never committed as an artifact — a number this document's own rule
says it may not carry. Removed rather than defended.)

⛔ **This table IS carried** — each figure is a field in a committed artifact
(named in its own row), which is the only reason it may stand here. What it does
NOT carry is a load-matched comparison: the two runs were taken on different boxes
under different load, so the RATIO bounds the mechanism (text length drives the
cost) and neither absolute is a clean-box latency. (An earlier version of this
paragraph called the table "an AD-HOC observation … at loadavg 2.7" — true of the
7.5 / 62.9 ms micro-benchmark the table REPLACED, false of the table itself, and it
contradicted the provenance paragraph seven lines above.)

**And three recorded gaps in the artifact:** `measurement-user-search.json` was
produced before the report's `config` carried `fused_rerank_gate_armed`,
`wired_pool_width` and the resolved `model`, so it has none of the three keys (its
`config.model` was filled in afterwards, as `model_provenance` states). Each
absence is expected rather than a lost record — and the numbers are themselves
evidence the baseline was not already reranked (`gained 165 / lost 0` is not
producible by a rerank-vs-rerank comparison). A re-run records all three fields.
`config.model` there reads `cross-encoder/ms-marco-MiniLM-L6-v2`, the id the run
resolved, and `model_provenance` states that it was filled in after the run and
that no measured value changed; the harness writes the resolved id natively from
now on.

So the user-search rerank **improves ranking and is not affordable**: 3.4 s per
query on CPU, per query, in the interactive search path. **The lever earns its gate
on the noun path only.** This is a scoped recommendation, not a general one — and
the gate CANNOT enforce that scope: there is one process-wide variable at one wiring
point, and EVERY caller of this read is on it:

| surface | priced here? |
|---|---|
| user search (`tortoise_search` / the SDK read) | yes — §5 above, ≈3.4 s/query |
| the capture-time noun prior (`extractor_v2._fts_rows`, `limit=3`) | yes — §3/§6 |
| the **ask lane** (`tortoise/ask_lane.py` reaches the same read with the default `order_by="relevance"`, no recency, a non-empty question) | **no** — and its candidate text is captured TURNS, i.e. the passage-length class priced at 85.227 ms/pair, so arming the gate there costs the same ≈3.4 s/query it costs user search |

Every caller that must not have its page re-picked **opts out** via
`_rerank_candidates=False`, in three classes — and two product reads deliberately do
NOT, because the re-pick is the lever: `hosted_api`'s search route and
`selfhost_api`'s search. Three distinct mechanisms, and they are NOT the same fact:

- **Order re-owners** — `suggest_entry_points`, `issue_insight`, `recall_state`,
  `recall_gaps` and `volunteer_context` (which binds the read as a `_search_fn=` seam
  rather than calling it, so its opt-out travels in `_search_kwargs`) sort the whole
  pool downstream, and `search_sessions` re-sorts its pool by the PRE-rerank `rrf`
  score before slicing. A pre-cut reorder there can only move which ids survive
  `limit` — an unmeasured composition change (and for `search_sessions` it is a
  permutation that sort discards, so it cannot change the page at all and would pay a
  model load for nothing).
- **Consumed as a SET, never as a page** — `_review_pool` (a `{pid: …}` membership
  map; `review_add` orders its own suggestions) and `_resolve_subgraph_seed` (an
  expansion START set). Neither keeps an order, so "re-picked page" is not the harm
  there; the harm is membership, which is the same class the probes below guard.
  ⛔ Arithmetic also rules `_review_pool` out on its own: it reads `limit=200`,
  above any sane pool width, so its page cannot move.
- **Recorded-baseline harnesses** — the eval / A-B tools
  (`tools/longmem_eval/retrieve.py`, `tools/fusion_formula_ab.py`,
  `tools/ask_pool_admission_probe.py`) and the benchmarks
  (`benchmarks/run_report.py`, `benchmarks/load_test.py`) pin it for comparability
  against RECORDED numbers, exactly as the first three pin `_valid_time_rerank`. The eval / A-B harnesses
(`tools/longmem_eval/retrieve.py`, `tools/fusion_formula_ab.py`,
`tools/ask_pool_admission_probe.py`) and the benchmark harnesses
(`benchmarks/run_report.py`, `benchmarks/load_test.py`) pin it for comparability
**Membership probes**: `battery/runner/setup.py`'s
seed-manifest guard (`limit=20`) and `tools/why_layer_rate.py`'s support-entry
lookup (`limit=25`) ask "is this row in the returned page", read a page smaller than
the pool width, and would turn a re-picked page into a false `ConfigError` / a false
FAIL. Each skip is disclosed as `order_owned_downstream` **when the caller passes a
leg trace** — most of these call sites pass none, because there is no sink to pass, so
their skip is silent by construction rather than unrecorded. The capture prior is the
deliberate exception on the write path: it keeps the rerank, under its own
`OVERRIDES:` record.

⛔ And the composition change is **bounded by arithmetic, not by the sweep**: the
rerank permutes only the scored head, so a caller reading `limit >= _scored_n` gets
the same page whatever the rerank does — which is why the public reads at
`limit >= 50` are safe today. That bound moves with `TORTOISE_FUSED_RERANK_POOL`,
the knob this PR wires end to end; raise it above a caller's `limit` and that
caller's page composition starts moving. The opt-out list above is what keeps that
from being silent.

Default-off does not bound the blast radius of turning it on; it only means nothing
changes until someone turns it on for all three — one of which this calibration does
not price at all.

---

## 6. Latency, at the real pool size, on CPU

Measured over **39,930 pairs** in the arm-5 run (torch intra-op threads pinned to
**1**). The count is the arm's own arithmetic, so it is checkable against §2/§3:
**300 mentions × (15 + 40) = 16,500** exact-arm pairs + **426 variants × (15 + 40)
= 23,430** variant pairs, recorded in `arms.rerank.latency.pairs_scored` beside
`score_s` and the per-pair mean — the three reconcile (`5.179 ms × 39,930 =
206.8 s`). It is the 300-mention sample, not the 1,000-name population.

`measurement-noun-path.json` → `arms.rerank.latency`:

| | |
|---|---|
| **ms per pair** | **5.179** |
| pairs per mention at pool 40 | 40 |
| **added latency per mention at pool 40** | **≈ 207 ms** |
| added latency per mention at pool 15 | ≈ 78 ms |
| **cost at the production shape (15 queries)** | **≈ 3.1 s / capture** |

**Cost at the production shape:** a capture issues up to `max_queries=15` derived
object queries, so pool 40 costs **≈ 3.1 s added per capture** (pool 15 ≈ 1.2 s).
That is the price of the gate, and it is the reason the gate ships **OFF** and
opt-in rather than default-on.

**Model load is NOT a table cell, because it is not in the artifact.** A one-off observation on this box: ~6.4 s from the local HF cache, once, then cached by name across both gates. Both *noun-path* artifacts' `latency.note` says load is EXCLUDED from `ms_per_pair` (the user-search artifact carries no `note` at all — its arithmetic says the same thing: 2,685.59 s / 31,511 pairs = 85.227), so a load figure sits here as prose under the same label this document gives any unartifacted observation — it bounds the mechanism (a cold first call is expensive and one-off), not a number to quote.

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

⚠️ **§7 above answers the falsifier AS WRITTEN, and §8 then qualifies it in a way
that changes the recommendation rather than the measurement.** The falsifier compares
the rerank against **the unreranked order**; it does not ask whether the *model* is
the thing that produced the gain. §8 answers that, and the answer is no: a Jaccard
token compare matches every **exact-arm** recall figure (on the variant arm it is
not identical in either direction — it is better by 1 at pool 15 and by 2 at pool 40;
§8), matches-or-beats every nDCG figure, and wins the variant arm outright, at
~1/1,000th the cost. **So the gain is real and
the cross-encoder is not what earns it.** The gate stays OFF either way, and the
noun-path note above should be read as "this lever is worth turning on" **not** as
"this model is worth paying for".

### Residuals (stated, not hidden)

1. **The lexical-baseline control was RUN — see §8, and it does NOT rescue the
   lever.** It reproduces every EXACT-arm recall figure (the variant arm is where they differ — see §8's table), matches or beats every nDCG figure,
   and wins the variant arm, at **0.005 ms/pair vs 5.463 ms/pair**. The consequence
   is recorded in §8 rather than left as an open flag: on the noun path the
   cross-encoder is not the implementation that earns this lever. **The user-search
   surface's lexical control was NOT run** (it needs a second ~46-minute arm) and
   nothing in §5 is inferred from §8.
2. **The exact arm's query is byte-identical to the gold's name** (it is the
   stored name), so its 100 % is an upper bound on that surface, not an estimate
   of production precision.
3. **Surface variants are character-level only.** No morphological, reordering,
   or synonym perturbation was measured, so "99.5 %" describes separator/case
   noise, not paraphrase.
4. **Single corpus.** 300 mentions is a stratified sample of a recorded population.
   The §8 run's population is a **regenerated** one (`c9f290a2866ec996`) because
   this box's corpus grew after the §3 run (`0aa80dff278c4e58`); the unreranked
   baseline moved with it (nDCG@10 0.7847 → 0.7830, recall unchanged at 81.3 %),
   so **§3's absolute numbers and §8's are not pooled** — §8's comparison is
   internal to its own run, where all three scorers saw the same pools.
5. **`score()` is called once per mention over the whole pool** in this arm.
   Production calls it once per derived query too, so the latency maps directly —
   but the production caller also pays a batched content read, which is **not**
   included in the ms/pair figures.

---

---

## 8. The lexical control — residual 1, run, and it does NOT rescue the lever

§Residuals-1 named this the decisive missing experiment and said a follow-up should
run it first. It has now been run, over **the same pools in the same process**:
one `--rerank --lexical-baseline both` invocation runs the cross-encoder arm and
both lexical arms over the same deterministic fused read, so the comparison is
counterfactual rather than merely adjacent — same mentions, same pool, same cut,
same tie-break. ⛔ Each arm RE-DERIVES that read (the tool shares no capture), and
the evidence that the baselines really are the same pools is in
`measurement-lexical-control.json`:
`recall_3_before` is **244** in all three arms at pool 15 and **244** in all three
at pool 40, against `recall_3_after` of 277 / 277 / 277 and 300 / 300 / 300.

`LexicalScorer` implements the cross-encoder's own `score(query, contents)`
interface, so **only the scorer changes**. Two declared, untuned modes:

- `difflib` — `SequenceMatcher(None, a, b).ratio()` on lowercased text (character level).
- `overlap` — Jaccard over lowercased alphanumeric tokens (token level).

All three tables below are `measurement-lexical-control.json` — `arms.*.40` / `arms.*.15`
for the recall and nDCG cells, `arms.*.latency` for the per-pair cost.

| pool 40, exact arm | recall@3 | nDCG@10 | gained / lost | **ms/pair** |
|---|---|---|---|---|
| cross-encoder (`ms-marco-MiniLM-L6`) | 300/300 = **100.0 %** | 0.9865 | +56 / −0 | **5.463** |
| lexical `difflib` | 300/300 = **100.0 %** | **1.0000** | +56 / −0 | **0.031** |
| lexical `overlap` | 300/300 = **100.0 %** | **1.0000** | +56 / −0 | **0.005** |

| pool 15, exact arm | recall@3 | nDCG@10 | gained / lost |
|---|---|---|---|
| cross-encoder | 277/300 = **92.3 %** | 0.9135 | +33 / −0 |
| lexical `difflib` | 277/300 = **92.3 %** | **0.9233** | +33 / −0 |
| lexical `overlap` | 277/300 = **92.3 %** | **0.9233** | +33 / −0 |

| surface variants (426 pairs) | pool 15 | pool 40 |
|---|---|---|
| cross-encoder | 95.1 % → 99.8 % (+21 / **−1**) | 95.1 % → 99.5 % (+20 / **−1**) |
| lexical `difflib` | 95.1 % → 99.5 % (+21 / −2) | 95.1 % → 99.5 % (+21 / −2) |
| lexical `overlap` | 95.1 % → **100.0 %** (+21 / **−0**) | 95.1 % → **100.0 %** (+21 / **−0**) |

### What this establishes, exactly

**Recall is identical on the EXACT arm in every cell** — the cross-encoder recovers no
exact-mention candidate a Jaccard token compare does not. On the VARIANT arm it is
not identical, in either direction: at pool 15 the cross-encoder recovers 425/426
against `difflib`'s 424/426 and `overlap`'s 426/426, and at pool 40 it is 424/426 against 426/426 — so the
sentence below about nDCG is the one that carries the claim, and "identical"
belongs to the exact arm only. On **nDCG the lexicals are equal or better**
(1.0000 vs 0.9865 at pool 40; 0.9233 vs 0.9135 at pool 15), and on the variant
arm `overlap` is **strictly better** — 100.0 % with **zero** regressions where the
cross-encoder loses one row. And the cost difference is not marginal: **0.005
ms/pair vs 5.463 ms/pair, ~1,090×**.

**→ The cross-encoder does not earn its cost on the noun path.** The ranking gain
§3 measured is **real** and **reproduced**, and §4's mechanism claim ("lexical
near-identity, not semantics") is now **measured rather than inferred**: what the
207 ms/mention buys is token overlap that a string compare gives in 5 µs. This is
the reading residual 1 was written to test for, and the test has returned the
answer the residual predicted as the adverse case.

**What it does NOT say.** It does not say the *wiring* is wrong, and it does not
touch the gate's posture: the gate stays **OFF**, which is now doubly justified.
It does not license replacing `CrossEncoderScorer` in product code — that is a
product decision for the owner, and the lexical scorer here is a **control that
is deliberately not wired into `rerank_pool`** and reaches no product path. It
does not measure morphology, reordering, paraphrase or any semantic relation,
because **no arm here does**: every arm is a permutation over candidates the legs
already returned.

### The recommendation this supports

1. **The gate stays OFF.** Unchanged, and now for a measured reason.
2. **On the noun path the cross-encoder is not the right implementation.** A
   lexical reorder reaches the same or better ranking at ~1/1,000th the cost, so
   if this lever is ever turned on for link-before-create, it should be turned on
   with a lexical scorer, not the model. That is a product change and **is not
   made here**.
3. **On user search neither is affordable as wired** (§5): ranking improves, but
   the cross-encoder costs ≈ 3.4 s/query. The lexical control was **not** run on
   that surface — stated as a residual below rather than inferred from §5.

## Sources

- [Model card — `cross-encoder/ms-marco-MiniLM-L6-v2`](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2)
- [BEIR — Thakur et al., NeurIPS 2021](https://datasets-benchmarks-proceedings.neurips.cc/paper/2021/file/65b9eea6e1cc6bb9f0cd2a47751a186f-Paper-round2.pdf)
- [In Defense of Cross-Encoders for Zero-Shot Retrieval — arXiv 2212.06121](https://arxiv.org/abs/2212.06121)
- #11901 — the candidate-recall corpus and instrument this reuses
- #12186 — lever C1
