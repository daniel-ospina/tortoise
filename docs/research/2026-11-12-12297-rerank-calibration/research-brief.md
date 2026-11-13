# #12297 — does the cross-encoder rerank earn its latency?

**Lever C1 of #12186.** The reranker was already BUILT (`tortoise/rerank.py`,
`CrossEncoderScorer`, landed under #317/#1545) and **not wired**. The wiring
landed on this branch (`31cf88be51`) behind a gate that ships **OFF**
(`TORTOISE_FUSED_RERANK`); this document is the calibration that decides whether
it earns that gate. Machine-readable artifact beside it:
`measurement-noun-path.json`. The **user-search** artifact is **not attached** —
that arm was not run for this PR; §5 states it rather than citing a file.

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

⛔ **The pool-15 number is the ceiling, exactly.** Arm 1 measured **277/300**
golds inside a 15-window and **`miss_past_k = 0`**, so 92.3 % is the *maximum
recall@3 any reranker can reach at pool 15* — the other 23 names are never
returned by the legs at that width. The rerank landed on **277, not 276**. It
recovered **every single in-pool candidate that fusion had pushed past the cut**.

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

## 5. User search — **NOT MEASURED in this PR. State that, do not cite a file.**

**No user-search artifact is attached.** An earlier draft of this section pointed
at `measurement-user-search.json` and at "§5 of this document's results table";
neither existed — the file is not in this branch and this document had no such
table. That is a dangling citation in the one section whose entire job is to say
whether a second surface was measured, so it is corrected here rather than
quietly deleted: **the user-search arm was NOT run for this PR.**

The harness it would run through IS committed and is itself pinned:
`tools/rerank_calibration_12297.py` (with its declared query construction, its
pairing, and its in-run permutation assertion) and
`tests/test_rerank_calibration_12297.py`. Producing the artifact is one command
and needs the `embeddings` extra plus a live lane:

```bash
uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"
uv run python tools/rerank_calibration_12297.py --records 400 \
    --out docs/research/2026-11-12-12297-rerank-calibration/measurement-user-search.json
```

**What that does to the verdict: nothing — and that is deliberate.** §7 rests on
the noun path alone, where the shipped read was measured paired and
counterfactual on the real corpus. §4 already says these numbers are silent about
user search; this section now says the same thing about the *absence* of a
number, which is the honest form of the same claim. A reader must not be able to
infer "user search was measured and looked good" from this document, and with a
phantom filename they could.

---

## 6. Latency, at the real pool size, on CPU

Measured over **39,930 pairs** in the arm-4 run, torch intra-op threads pinned to
**1**:

| | |
|---|---|
| **ms per pair** | **5.179** |
| pairs per mention at pool 40 | 40 |
| **added latency per mention at pool 40** | **≈ 207 ms** |
| added latency per mention at pool 15 | ≈ 78 ms |
| model load (from local HF cache) | 6.4 s, once, cached by name across both gates |

**Cost at the production shape:** a capture issues up to `max_queries=15` derived
object queries, so pool 40 costs **≈ 3.1 s added per capture** (pool 15 ≈ 1.2 s).
That is the price of the gate, and it is the reason the gate ships **OFF** and
opt-in rather than default-on.

This is a **CPU** figure. It is not comparable to the model card's
"1800 docs/sec" (computed on a **V100**), and quoting that number as ours would
be a category error — it is ~5,400× the throughput measured here.

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
