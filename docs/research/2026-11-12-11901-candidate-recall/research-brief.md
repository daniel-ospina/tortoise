---
title: "Candidate recall@k for the capture-time noun-matching prior — measured, and the decision it supports (#11901)"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-12
updated: 2026-11-12
subjects.team: epistemic-team
issue: "#11901"
aboutSubjects: Tortoise S3 link-before-create prior, entity resolution candidate generation, extractor v2 write path
aboutObjects: STORAGE-ARCHITECTURE.md §14.3 V3, SEARCH-ARCHITECTURE.md V1, research-brief #2730 entity attachment, #11395 KEEP ruling
---

# Candidate recall@k for the capture-time noun-matching prior

**Issue:** [#11901](https://github.com/daniel-ospina/tortoise/issues/11901) — V3's work item: fix the noun-matching
prior before any index. · **Decision this feeds:** `STORAGE-ARCHITECTURE.md` §14.3 **V3** (the ruling is KEEP,
2026-11-10; what survives is *how the kept input is used*).
**Harness:** `tools/candidate_recall_11901.py` · **Raw artifact:**
`measurement.json` (this directory) · **Measured:** 2026-11-12, population 1,000 names / stratified sample 300.

---

## 0. Preflight

**Search tool:** rung 1 (`mcp__seo-intelligence__perplexity_search`) is **unavailable on this box** —
`mcp_load seo-intelligence` → `Unknown MCP server 'seo-intelligence'`. Rung 2 (`web_search`) also refused with
`PERPLEXITY_API_KEY not set`, so the calls were made against the Perplexity API directly with the model named
explicitly on every request (`"model": "sonar"`; no `sonar-deep-research`, no `sonar-reasoning-pro`). **Queries:
5 attempted / 5 returned.** No 429.

**Graph:** the brief for #11901 names node ids on a **local** graph (`docker://:@localhost:16379/tortoise`); that
graph is **not running on this box** (nothing listens on 16379, no container carries it) and every one of the six
named ids answers `GRAPH-EMPTY-RESULT` — they belong to a graph this box cannot reach. ⚠️ **The brief's premise
that "hosted is not configured" is stale**: the hosted graph answers `HTTP 200` with `~/keys.env`
`TORTOISE_MCP_API_KEY` sourced (375 KB `/v1/context`). It is the fleet's *memory* graph, not the product entity
graph, so it does not hold the entity-resolution research — **5 domain-vocabulary queries, 0 relevant hits**: it
carries Points about lanes and merges, no `:Object` census, no V3 measurement. Disposition: **the internal record
that answers this issue is the DOCS, which are read in full below**; the graph contributed nothing to the finding
and is recorded as read-but-empty rather than skipped. Wrote back: see §7.

---

## 1. What the prior is, in code

| step | where | what it does |
|---|---|---|
| derive | `tortoise/extractor_v2.py:2809` (`_derive_queries`) | one `object` query per extracted entity name, sanitised (`:2776`) |
| look up | `:2880` (`_fts_rows`) | `sdk.tortoise_fts_query(query, entity_type="object", limit=limit, …)`, `limit=3` default |
| accumulate | `:3074` (`results[bucket[entity_type]][rid] = row`) | **every** derived query's rows land in ONE id-keyed dict |
| bind | `:5762` (`_find_existing_entity`) | **exact** `_norm` name equality (whitespace-collapse + lowercase, `:4225`) with a kind-compatibility filter; `ambiguous → None` |
| adjudicate | `:5938` (`resolve_entities`) | phase 2 LLM SELECT over the candidate table, only when a model is supplied and the query used the *stored* form |

Two consequences decide the shape of the measurement, and neither was on the issue:

1. **`limit=3` is not "3 candidates".** `search_graph` unions the rows of *every* derived query into
   one set, so a capture with 15 entities hands `resolve_entities` the union of 15 windows. **Measured: mean
   31.9 candidates, min 24, max 43** (`arms.pooled`). The issue's comparison — *"our window is 3, Zep's is 15"*
   — compares a per-query width against a candidate-list length. The right comparison is **~32 vs 15**.
2. **The matcher is exact**, so a candidate can be *inside* the window and still unusable. Retrieval failure and
   matching failure have different fixes and are measured separately below.

---

## 2. Method, and what is real

The harness runs the real code path: names harvested from this box's **real capture corpus**
(`~/.tortoise/session-events`, 9 daily files, 8,218 real sessions, 45.3 M characters) into a **real FalkorDB**
through the real projection (`_upsert_object`, id-keyed per `#3590`), queried by `sdk.tortoise_fts_query` with the
exact flags `_fts_rows` passes (`include_terminal=True`, `_record_reads=False`, `_valid_time_rerank=False`) and
the exact query sanitisation production applies.

**Harvest rules** (declared, deterministic, in the tool): `the <1-4 tokens>` — the class **62.3 %** of `:Object`
names belong to (`docs/research/2026-09-24-2730-entity-attachment/research-brief.md` §3.2) — plus the three
identifier shapes the same audit enumerated: `#<n>`, repo-relative paths, `snake_case`. Frequency-ranked by
*turn* count, because a name that recurs across captures is the one link-before-create exists to re-identify.
Population 1,000 (60 % definite / 30 % ident / 10 % path); sample 300, **stratified by class** so one shape
cannot be reported as the population's.

⛔ **Declared gap, stated where it cannot be skimmed away:** this is a sample of the strings real captures
contain, **not a census of production `:Object` rows**. No lane can reach that population (there is no live
`:Object` graph on this box; the hosted graph carries memory Points, not product entities), and §14.3's own
`--census` note records the same limit for the vector population. Every number below measures **the retrieval and
matching mechanism on a real name distribution**, never production.

---

## 3. Arm 1 — the mention IS the stored name (retrieval)

`recall@k` = the fraction of names for which the exact node appears in the top-`k` window. Misses are split into
the two states with different fixes.

| k | recall@k | miss: past THIS k, present at 15 | miss: absent even at k=15 | of which leg returned 0 rows |
|---|---|---|---|---|
| **3** | **164/300 = 54.7 %** | 32 | 104 | 11 |
| 5 | 178/300 = 59.3 % | 18 | 104 | 11 |
| 10 | 189/300 = 63.0 % | 7 | 104 | 11 |
| 15 | 196/300 = 65.3 % | 0 | 104 | 11 |

**By class** (`arms.exact_by_class`):

| class | n | recall@3 | recall@15 | zero-row queries @3 |
|---|---|---|---|---|
| definite description | 180 | 136 = **75.6 %** | 156 = 86.7 % | 3 |
| identifier (`#n`, `snake_case`) | 90 | 19 = **21.1 %** | 20 = 22.2 % | 8 |
| path | 30 | 9 = **30.0 %** | 20 = 66.7 % | 0 |

**What this says.** The window does lose the exact match — **45.3 % of the time at `limit=3`** — but **widening it
is a weak lever and a class-specific one**: 3 → 15 recovers 32 of 136 misses (**23.5 %**), takes the definite class
from 75.6 % to 86.7 %, takes paths from 30 % to 67 %, and moves the identifier class by **one name in ninety**.
**104 misses (34.7 % of the sample) are absent from even a 15-window** — no `limit` reaches them.

The identifier class is the finding that most needs care: 8 of 90 returned **literally no rows** for their own
exact name, and the class sits at 21 %. **The mechanism is NOT established here.** A 5-node probe with the same
call shape returns `#11901` and `keep_numeric` makes no difference (`debug probe`, not committed), so the
zero-row cases are a property of the populated graph — most plausibly fusion/selection under a crowded index —
and this document does not claim a cause it did not measure. **Filed as the open question in §6.**

⚠️ `mean rank` when found is 1.37 at k=3 (**`arms.exact`**) — the leg is not scattering good answers; it is
*ranking them out* or not producing them. The 53 recorded k=3 misses share a mean of **8.5 population names with
≥2 tokens in common** (median 5), which is the crowding the research brief predicted for this exact class (*"they
cluster tightly in vector space and block badly lexically"*, §12.2c): `the work` / `the work is finished` / `the
work you were given` occupy the same three slots.

---

## 4. Arm 2 — the mention is a VARIANT of the stored name (matching)

The node exists; a later capture says it a different, real way. This is the duplicate-manufacturing class, and the
transformation set is the one `_norm` does **not** fold — article, separator/hyphen, path basename, `#`:

| k | pairs | canonical **in** window | **attached** by the shipped matcher | attached under a candidate punctuation fold |
|---|---|---|---|---|
| **3** | 419 | 202 = 48.2 % | **0 = 0.0 %** | 53 = 12.6 % |
| 5 | 419 | 219 = 52.3 % | **0 = 0.0 %** | 55 = 13.1 % |
| 10 | 419 | 237 = 56.6 % | **0 = 0.0 %** | 60 = 14.3 % |
| 15 | 419 | 247 = 58.9 % | **0 = 0.0 %** | 62 = 14.8 % |

**Decomposed, and this is the load-bearing table** (`arms.variant_by_class`, k=3 → k=15):

| variant class | pairs | in window @3→@15 | attached by shipped matcher | attached under punctuation fold |
|---|---|---|---|---|
| article dropped (`the plan doc` → `plan doc`) | 179 | 122 → 154 | **0 → 0** | **0 → 0** |
| separator (`the admin-merge rail` ⇄ `the admin merge rail`) | 193 | 53 → 62 | **0 → 0** | 53 → 62 |
| path basename only | 30 | 11 → 14 | 0 → 0 | 0 → 0 |
| `#` dropped (`#11901` → `11901`) | 17 | 16 → 17 | 0 → 0 | 0 → 0 |

**What this says.** Widening the window from 3 to 15 raises the share of variants whose canonical is *present*
from 48.2 % to 58.9 % and changes the attach rate by **exactly zero**. Every one of the 202 in-window pairs at k=3
is discarded by the matcher. The `limit` is not the constraint for this class, at any width; **the normalisation
is**. A punctuation fold recovers **100 % of the in-window separator variants** (53/53 at k=3, 62/62 at k=15) and
**0 %** of the article class — an article is not punctuation, and that rung is the embedding/vector leg's job by
design (research-brief §6 ladder: exact → alias → embedding → adjudication).

---

## 5. The duplicate rate, stated as the three configurations it was measured in

The issue asks for the duplicate-noun rate "with the prior on and off (or with the window widened)". The arms above
give it directly, on the same 419 variant pairs:

| configuration | attaches | would mint a second node |
|---|---|---|
| **prior off** (no link-before-create) | 0 % | 100 % |
| **prior on, `limit=3`, shipped matcher** (today) | 0.0 % | **100 %** |
| **prior on, `limit=15`, shipped matcher** | 0.0 % | **100 %** |
| prior on, `limit=3`, + punctuation fold (NOT shipped) | 12.6 % | 87.4 % |
| prior on, `limit=15`, + punctuation fold (NOT shipped) | 14.8 % | 85.2 % |

⚠️ Read the "prior off" row as the definition of the ceiling, not as a measurement of a shipped configuration: with
no prior, *every* mention is new by construction. The measured statement is the pair of middle rows — **today's
prior and a 5× wider one are indistinguishable on the class that manufactures duplicates.**

---

## 6. External findings, each through the contradiction test first

The recorded decisions covering this work, and what none of these findings may override:

- **(1)** Owner ruling 2026-11-10 (`#11395` comment `6781921075`): *"`:Object` and `:Subject` keep their stored
  embedding … No pass may remove those embeddings, and no pass may stop writing them."*
- **(2)** §12.2c: the entity vector is the **entry + resolution key**, *"budget-capped as a seed index"*,
  sequenced **downstream of the extractor work**.
- **(3)** `SEARCH-ARCHITECTURE.md` V1: the index and its payload live in Postgres; the text index stays in the graph.

**No recorded decision reaches the size of the candidate window or the verdict step** — those are this issue's
live question, which is why they were measured rather than researched into existence.

| finding | source | contradicts (1)/(2)/(3)? | verdict |
|---|---|---|---|
| **Zep/Graphiti** searches **up to 15 candidates** at cosine **≥ 0.6**, and **the threshold only generates candidates — it does not decide**; a deterministic similarity resolver or an **LLM** chooses the existing node or declares a new one | arXiv 2501.13956 (`arxiv.org/html/2501.13956`); `graphiti_core/utils/maintenance/node_operations.py` | No | **ADOPT the shape, not the number.** The two-stage split is already ours (`_find_existing_entity` → phase-2 SELECT). Copying "15" is **counter-indicated by measurement**: our per-query width is 3 but our effective list is ~32, so "15" is a *narrowing* for us. |
| **Mem0** is ADD-only: no similarity cutoff merges memories; an extracted entity links to an existing one by **exact name or cosine ≥ 0.95**, and that is a *link*, not a merge | `docs.mem0.ai/core-concepts/how-it-works.md`, `/memory-operations/add` | No | **NOT ADOPTED as a threshold.** 0.95 would be a **second constant** beside `tortoise.embeddings.DEFAULT_THRESHOLD = 0.72` (`tortoise/embeddings.py:763`), and *"a second constant for a similarity threshold"* is a **REFUSED** row in research-brief §5. The *shape* (link, never merge) is ours already. |
| **pgvector filtered ANN**: a `WHERE` predicate is applied **after** the ANN scan; a selective predicate over-filters and returns fewer than `LIMIT` or misses true neighbours; `hnsw.iterative_scan` (≥ 0.8) keeps scanning, and **does not** turn it into exact filtered NN | `docs.pgedge.com/pgvector/v0-8-7/{filtering,iterative-index-scans}`, supabase HNSW guide | No | **NOT THIS PR'S STEP, and it binds step 2.** It is the same trap our own graph point records (post-filter after ANN). It is irrelevant at the measured population: a scan is exact and an approximate index is parameters + rebuild + lost exactness for nothing (§12.1a size-earned rule). It stays the named risk **when** the index is built. |
| **LongMemEval** publishes no isolated per-component ablation for entity resolution or write-time dedup; its reported gains are in **indexing and query formulation** (finer memory units, fact-augmented keys, time-aware expansion) | arXiv 2410.10813 (`arxiv.org/html/2410.10813`) | No | **CONFIRMS the gap this issue exists to close**: there is no external number to borrow, so the measurement had to be ours. `docs/benchmarks/longmemeval-500q/run-report.md` stays `PENDING` — **no LongMemEval figure of our own is claimed anywhere in this document.** |
| **Blocking recall degrades with scale and with surface variation**: token/lexical blocking falls ~0.91 → ~0.55 as records go 10 K → 2 M (block-size caps); embedding blocking reaches ~89.9 % pair completeness (so ~10 % missed); a single HNSW index measured 97.7 % | arXiv 2202.12521; arXiv 2607.26298; Mannheim SC-Block (ESWC 2024) | No | **ADOPT as the frame, not as a number**: our 54.7 % recall@3 on the exact arm is *worse* than published lexical blockers and the paper's own diagnosis (block-size caps) matches ours (crowding, 8.5 siblings). It is why "widen `k`" reads as the obvious fix and why the measurement was needed to show it is not. |

**Adoption gate:** `adopt` (the two-stage generate-then-decide shape; the blocking-recall frame) · `no recorded
decision reached` for the window size and the verdict step — that is #11901's live question and it is now
measured · `refuse` (a second similarity constant — Mem0's 0.95; and building the index before the resolution
step — §12.1a + §14.3 V3's own sequencing) · `no reopen` — nothing here contradicts (1), (2) or (3).

---

## 7. The decision the evidence supports

**1. Do not widen `limit` on its own. The prior should not change yet on a window argument.**
It recovers 32 of 136 exact-arm misses (54.7 % → 65.3 %), moves the duplicate-manufacturing class by **0.0 %**,
and pushes the effective candidate list from ~33 toward ~150 — straight into research-brief **X7**'s regime where
selection accuracy falls as the candidate list grows. A wider window **without** a verdict step is the recorded
regression; the measurement says the window is not even the right first lever.

**2. The measured next change, in this order, and it is not shipped here:**
  - **(2a) the matcher's normalisation** — a separator fold in the identity comparison: **0 % → 100 %** of
    in-window separator variants, **12.6 % → 14.8 %** of variants overall, at **+0.3 pp** available from widening
    `k`. Cheap, conservative (it cannot merge two different names by itself), and it is the alias *rung* the
    research brief already places at rung 2. ⚠️ `_find_existing_entity` is not the only consumer of `_norm`, so
    the fold belongs in the comparison — **not** in `_norm`.
  - **(2b) the identifier class** — 21 % recall@3 with 8/90 zero-row queries and **no** mechanism established.
    An investigation with its own fixture, because this class is minted constantly and the loss is invisible.
  - **(2c) the embedding rung for the article class** — 179/419 pairs that neither the window nor a punctuation
    fold reaches. This is §12.2c's *resolution key*, i.e. what the KEEP ruling's embeddings are FOR; it runs on
    `DEFAULT_THRESHOLD`, not a new constant.

**3. The index question, answered on the evidence rather than before it: NOT YET, and not because of this
measurement alone.** At the measured population a scan is exact; the arms were run against an **unindexed** label
(the `#4997` full-scan notice fires on every read) and the whole 300-name × 4-width × 3-arm sweep plus the harvest
completed in **~22 s**. §12.1a's size-earned rule, §14.3 V3's sequencing and the pgvector post-filter trap all
point the same way: resolution step first, index when volume earns it.

---

## 8. What this document does NOT claim

- **Not a production census.** The population is a declared sample of real capture strings (§2).
- **No duplicate *rate* for production.** 419 variant pairs at 0 % attach is the mechanism; the production rate
  also depends on how often a capture actually says a variant, which this harness does not measure.
- **No mechanism for the identifier class.** §3 measures it and §7-2b files it; nothing here diagnoses it.
- **No LongMemEval number.** `run-report.md` stays `PENDING`; the external finding is that LongMemEval has no
  per-component ablation to borrow.
- **No claim that the priors' window is the product's main retrieval cost.** This is the *capture* path only.

## 9. Reproduce

```bash
uv run python tools/test_lane.py run -- tools/candidate_recall_11901.py \
    --population 1000 --sample 300 --captures 20 \
    --out docs/research/2026-11-12-11901-candidate-recall/measurement.json
```

`tests/test_candidate_recall_11901.py` pins the structural facts this document rests on (the union across derived
queries; the exact matcher; the variant classes), that the committed artifact is internally consistent, and that
the tables above match it — so the numbers cannot rot away from the evidence.

## 10. Write-back

`wrote: not written — the local graph the brief names is absent on this box (nothing listens on 16379; its six
named ids answer GRAPH-EMPTY-RESULT), and the hosted graph REFUSED the write: `tortoise-graph.sh evidence …` →
`{"error":"Team points limit reached (25000). Upgrade your plan to increase it.","code":-32006}`.` The finding
therefore lives in this document, in `measurement.json`, and on #11901 — and the point that would have carried it
is blocked on a quota, not on a decision. `edges: none — no hosted point on this topic exists to attach to
(the topic lives on the absent local graph), so an edge would have been fabricated.`
