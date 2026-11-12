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
**Harness:** `tools/candidate_recall_11901.py` · **Artifacts, this directory:** `measurement.json` (**hybrid**, the
headline) and `measurement-fts-only.json` (the lexical-only control) · **Measured:** 2026-11-12, population 1,000
names / stratified sample 300, both modes on one box.

---

## 0. Preflight

**Search tool:** rung 1 (`mcp__seo-intelligence__perplexity_search`) **could not be loaded on this box** —
`mcp_load seo-intelligence` → `spawn /mcp-servers/seo-intelligence/node_modules/.bin/tsx ENOENT`. The server IS
declared (`mcp_catalog` lists it as `lazy`), and the catalog's own note names the cause: it needs a one-time
`npm install` in `mcp-servers/seo-intelligence` per checkout, or the entry fails to spawn. (An earlier draft
recorded this as `Unknown MCP server 'seo-intelligence'`, which points a fixer at the wrong layer — the config.)
Rung 2 (`web_search`) also refused with `PERPLEXITY_API_KEY not set`: the key exists in `~/keys.env` (mode 0600),
which the research skill names as its home, but it was not in this shell's environment — so the calls were made
against the Perplexity API directly with the model named
explicitly on every request (`"model": "sonar"`; no `sonar-deep-research`, no `sonar-reasoning-pro`). **Queries:
5 attempted / 5 returned.** No 429.

**Graph:** the brief for #11901 names node ids on a **local** graph (`docker://:@localhost:16379/tortoise`); that
graph is **not running on this box** (nothing listens on 16379, no container carries it) and all six named ids
answer `GRAPH-EMPTY-RESULT` — they belong to a graph this box cannot reach. ⚠️ **The brief's premise that "hosted
is not configured" is stale:** the hosted graph answers `HTTP 200` with `~/keys.env` `TORTOISE_MCP_API_KEY`
sourced (375 KB `/v1/context`). It is the fleet's *memory* graph, not the product entity graph, and it holds none
of the entity-resolution research — **5 domain-vocabulary queries, 0 relevant hits**. Disposition: the internal
record that answers this issue is the DOCS, read in full below; the graph is recorded as **read-but-empty**, not
skipped. The write-back was attempted and refused — §10.

---

## 1. What the prior is, in code

| step | where | what it does |
|---|---|---|
| derive | `tortoise/extractor_v2.py:2809` (`_derive_queries`) | one `object` query per extracted entity name, through `_sanitise_query_text` (`:2777`) |
| look up | `:2880` (`_fts_rows`) | `sdk.tortoise_fts_query(query, entity_type="object", limit=limit, …)`, `limit=3` default — a **fused** read (fts + vector + structural) |
| accumulate | `:3074` (`results[bucket[entity_type]][rid] = row`) | **every** derived query's rows land in ONE id-keyed dict |
| bind, phase 1 | `:5762` (`_find_existing_entity`) | **exact** `_norm` name equality (whitespace-collapse + lowercase, `:4225`) with a kind-compatibility filter; `ambiguous → None` |
| adjudicate, phase 2 | `:5938` (`resolve_entities`) | the LLM SELECT fires on `if model is not None and existing and unmatched:` (`:6030`) — **and the production capture path passes the model unconditionally** (`:14422`), so phase 2 is LIVE in production, not an opt-in |

Three consequences decide the shape of the measurement, and none of them was on the issue:

1. **`limit=3` is not "3 candidates".** `search_graph` unions the rows of *every* derived query into one set.
   **Measured at the `max_queries=15` budget** (hybrid, 20 captures — the whole sweep, not the k=3 case):

   | per-query `limit` | mean candidates | min | max |
   |---|---|---|---|
   | **3** | **40.4** | 31 | 44 |
   | 5 | 64.3 | 46 | 72 |
   | 10 | 121.2 | 80 | 138 |
   | 15 | 170.8 | 100 | 198 |

   The issue's comparison — *"our window is 3, Zep's is 15"* — compares a per-query width against a candidate-list
   length. At production's budget cap the right comparison is **~40 vs 15** — and ⚠️ **the cap is the WIDEST case,
   not the typical one.** The artifact records the caveat in `pooled_budget_note` (*"a capture with 5 entities
   issues 5 object queries"*). At `limit=3` that is 807 rows over 20 groups of 15 names ≈ **2.7 candidates per
   derived query**, so a 5-entity capture's union is ≈ **13 < 15**: Zep's 15 **narrows** us at or near the cap and
   **widens** us below ~6 entities. What holds at every capture size is the mechanism, not the arithmetic — the
   candidate set is the union over the derived **object** queries (`_derive_queries` emits no `subject` query, and
   `search_graph` buckets `object` rows into `entities` at `:3074`; the `point`/`event` rows land in other
   buckets and are not handed to `resolve_entities`), never `limit`.
2. **The phase-1 matcher is exact**, so a candidate can be *inside* the window and still unusable. Retrieval and
   phase-1 matching fail differently and are measured separately (§3, §4).
3. **Phase 2 exists, is instructed to do exactly this job** (*"Resolve ONLY confident aliases: same referent,
   case/variant/abbreviation forms"*, `:5804`), and is handed the candidate table. **This document does not
   measure phase 2** — its arm is phase 1. §4/§5 say so in their headings, and §7 names phase 2's measurement as
   the next work.

---

## 2. Method, and what is real

The harness runs the real code path: names harvested from this box's **real capture corpus**
(`~/.tortoise/session-events`, 9 daily files — the count the artifact carries, as `corpus.files`) into a **real
FalkorDB** through the real projection (`_upsert_object`, id-keyed per `#3590`), queried by `sdk.tortoise_fts_query`
with the exact flags `_fts_rows` passes (`include_terminal=True`, `_record_reads=False`,
`_valid_time_rerank=False`) and production's own query sanitisation.

⚠️ **The record and character totals are NOT in the artifact and do not reproduce from it:** read on that same
directory at 2026-11-12T15:48Z it held **7,859 records / 53,089 turns / 42.4 M conversation characters**, so "the
corpus census" depends on the box *and* on which of those quantities is meant. Re-derive it rather than quoting
it; no figure below depends on it. See also §2.2 — the population itself is re-provable only going forward.

### 2.2 The graph the population was written to (and why a re-run now refuses to guess)

⚠️ **A re-run must NOT reuse the graph.** `_upsert_object` MERGEs on the **id** and writes `o.name` **ON CREATE
only** (`tortoise/projection/entities.py:5031-5050`, the recorded `#8919` gap), so a second run whose harvest
shifted a name into a different population position leaves the node at that id carrying the FIRST run's name —
the query then matches nothing and books as `miss_absent_at_max_k`. Measured on a lane container: upsert id `X`
as `alpha original`, upsert the same id as `beta shifted`, and `beta shifted` returns **zero rows** while `alpha
original` still returns `X`.

⛔ **The two committed artifacts predate both guards**, so their population is not re-provable from the artifact:
neither records the graph it wrote to nor a population digest, and both default to the same
`org_recall11901`-derived graph. A positional shift (a drifted corpus, a different `--population`) would leave a
stale name at that id and **understate** recall — in the absence direction, which is the headline. The harness
now (a) targets a **run-scoped graph** by default, (b) records `corpus.graph` and a population digest, and (c)
**refuses** the run when a stored name does not match the population it just built. Treat the committed figures as
the measurement they are — with this residual declared, in this direction, rather than as a property a reader can
re-derive from the JSON alone.

**Harvest rules** (declared, deterministic, in the tool): `the <1-4 tokens>` — the class **62.3 %** of `:Object`
names belong to (research-brief §3.2) — plus an **identifier** class (`#<n>` references, which the audit splits into
bare `#<n>` 0.5 % and `PR #<n>`/`issue #<n>` 1.7 %, and `snake_case` names, which sit inside its 9.0 % "contains a
code token" row) and a separate **path** class (repo-relative paths, for which §3.2 records no share at all). Frequency-ranked by **session-record** count — the number of distinct capture records a name appears in, not its
turns and not its raw occurrences (a session repeating a phrase 40 times in one turn is one mention).

**Population 1,000, stratified sample 300.** ⛔ **The class quotas are a deliberate OVERSAMPLE of the minority
shapes, not the audit's shares.** The audit measured definite descriptions at 62.3 % of real `:Object` names and,
separately, `#<n>`-shaped references at ≈2.2 % (0.5 % bare `#<n>` + 1.7 % `PR #<n>`); it records **no path share**,
and its 9.0 % row ("contains a code token") is a superset of `snake_case`. This population gives the identifier
class 30 % and the path class 10 % — **two separate classes** the tool harvests — so each carries enough names for
a usable per-class rate. **The composite headline is stratified,
never class-share weighted** — it is not an estimate of the production mix, and the per-class table is the number
to quote for any one class.

⚠️ **The corpus is LIVE, and the two runs therefore did NOT sample the identical population.** `~/.tortoise/session-events`
is written by the fleet's own capture hook: the two committed artifacts harvested **102,898** (hybrid) and
**103,087** (control) distinct definite descriptions, a 0.18 % drift between two runs ~60 s apart.

⛔ **That 0.18 % is the gap between THOSE two runs, not a bound on between-run variation, and this corpus is NOT
append-only in effect.** Re-running the committed `harvest()` over the same nine files ~2 h later (16:37Z and
16:41Z, twice, independently) returned **91,725 / 91,774 / 91,601** distinct definite descriptions — **−10.8 %**
— *fewer* than the artifacts, which appends alone cannot produce (ident −6.5 %, path −7.3 % as well). The obvious
mechanisms were then RULED OUT by measurement rather than assumed: the eight older day-files are frozen at their
day-end mtimes, today's file is append-only (**+60 distinct definite names in 180 s**, 1,155 → 1,165 lines),
`tortoise/session_events_drain.py` is not running, and the directory holds no sealed or rotated file. So the loss
pre-dates that window, and **the current contents of the named directory cannot reproduce the artifacts' population
at all** — either today's file was rewritten between 14:19Z and 16:37Z (an event no longer observable) or the
artifacts' bytes came from a corpus other than the one they name; nothing in the artifact distinguishes the two.
The artifacts carry their own `corpus.harvested_distinct`, so the drift is at least visible — and it is why
**§3.1's class deltas are BETWEEN-RUN, not paired**: treat them as directional evidence, not a controlled effect
size, and see the consequence in §3.1.

⛔ **Declared gap, stated where it cannot be skimmed away:** this is a sample of the strings real captures
contain, **not a census of production `:Object` rows**. No lane can reach that population (no live `:Object` graph
on this box; the hosted graph carries memory Points, not product entities), and §14.3's `--census` note records
the same limit for the vector population. Every number below measures **the retrieval and matching mechanism on a
real name distribution**, never production.

### 2.1 Both retrieval modes are measured, because the mode changes the answer

The capture prior is the **fused** read. A venv without the `embeddings` extra has
`EmbeddingModel.get() → None` and the fused read degrades to **keyword-only** — the class
`battery/runner/retrieval_preflight.py` names (*"the numbers were published as product measurements"*). So both
modes were measured and both artifacts are committed; each records which mode ran in its `retrieval` block
(`has_embedder`, `legs_seen`), and the note there is derived from the **legs that actually ran**, not from the
embedder being available — a run whose dense leg never answered a single query is labelled
`KEYWORD-ONLY DESPITE AN EMBEDDER`, because that is a different measurement again. ⚠️ `legs_seen` answers *did this
leg run at all during the sweep*, not *on how many queries*: the harness accumulates each leg once-True across
every query of the sweep, so a leg that answers the first few and then fails (an encode failure, an open breaker)
still labels the whole run `hybrid`, and `retrieval` carries no per-query failure count to see it. The label is a
floor, not a certificate; a per-query denominator would need the artifacts regenerated. The two runs sampled the same
live corpus but not the identical population (§2).

⚠️ **What the committed `retrieval.note` can and cannot prove.** The artifacts' label is derived from
`legs_seen`, which is the **OR** over every window of a predicate that asks whether the vector leg was
*submitted* (`ran`). That is the **#3005 battery capability predicate**, and `search_engine.require_hybrid_read`
says explicitly that *"a battery lane that records a score should delegate to this predicate so the capability
it reports cannot be looser than this one"* — it asks for an IN-SCOPE vector entry that RAN and was **NOT
degraded**, because `ran=True, degraded=True` (`no_embeddings`, below the relevance floor) and `ran=False,
reason="breaker_open"` both contribute no semantic results. ⛔ **The committed artifacts predate the strict
test**, so a run in which some windows degraded to keyword-only would still have carried the label `hybrid` — and
the artifacts cannot answer the strict question after the fact. A re-run now classifies **per window** with
`require_hybrid_read`, records `retrieval.window_modes`, and **refuses to write an artifact** whose windows are
not uniformly one mode; treat the committed label as `legs_seen`-derived, with this residual declared.

---

## 3. Arm 1 — the mention IS the stored name (retrieval)

`recall@k` = the fraction of names for which the exact node appears in the top-`k` window. Misses are split into
the two states with different fixes.

**HYBRID — the product's read (`measurement.json`)**

| k | recall@k | miss: past THIS k, present at 15 | miss: **absent even at k=15** | leg returned 0 rows |
|---|---|---|---|---|
| **3** | **184/300 = 61.3 %** | 35 | 81 | 0 |
| 5 | 195/300 = 65.0 % | 24 | 81 | 0 |
| 10 | 207/300 = 69.0 % | 12 | 81 | 0 |
| 15 | 219/300 = 73.0 % | 0 | 81 | 0 |

**KEYWORD-ONLY — the lexical control (`measurement-fts-only.json`)**

| k | recall@k | past THIS k | absent even at k=15 | leg returned 0 rows |
|---|---|---|---|---|
| **3** | **166/300 = 55.3 %** | 28 | 106 | 11 |
| 5 | 178/300 = 59.3 % | 16 | 106 | 11 |
| 10 | 189/300 = 63.0 % | 5 | 106 | 11 |
| 15 | 194/300 = 64.7 % | 0 | 106 | 11 |

### 3.1 The finding that matters most: the dense leg moves the classes in OPPOSITE directions

⚠️ **Between-run, not paired** (§2): the two artifacts sampled the same live corpus ~60 s apart, not the identical
population. The direction of every delta below is large relative to the 0.18 % those two runs differ by — **but §2
measures the population itself moving by ~10.8 % between a re-run and the artifact, which is the SAME ORDER as the
−13.3 pt definite delta this section defends.** So that delta is directional, not a controlled effect: what makes it
usable is its sign and its size across two independent runs, not a claim that between-run variation is small.

| class | n | keyword-only @3 | **hybrid @3** | change | hybrid @15 |
|---|---|---|---|---|---|
| definite description | 180 | 137 = 76.1 % | **113 = 62.8 %** | **−13.3 pts** | 116 = 64.4 % |
| identifier (`#n`, `snake_case`) | 90 | 19 = 21.1 % | **42 = 46.7 %** | **+25.6 pts** | 73 = 81.1 % |
| path | 30 | 10 = 33.3 % | **29 = 96.7 %** | **+63.3 pts** | 30 = 100 % |

**The dense leg helps identifiers and paths enormously and makes the definite-description class worse.** That is
not a surprise in hindsight — it is §12.2c's own recorded warning, now measured: *"62.3 % of those names are bare
definite descriptions … they cluster tightly in vector space"*. In an RRF fusion those near-synonyms compete with
the exact node for the same slots, and `the work` / `the work is finished` / `the work you were given` crowd each
other out. ⚠️ **This is also why no single `limit` fixes the prior** — the two classes need opposite treatment, and
a width chosen for one is wrong for the other.

**What the width does.** Hybrid: 3 → 15 recovers **35 of 116 misses (30.2 %)**; **81 misses (27.0 % of the sample)
are absent from even a 15-window.** Keyword-only: 28 of 134, with 106 absent at 15. Mean rank when found is **1.2 at k=3** and 1.4 at k=5 (hybrid) — the leg is not scattering good answers, it is
ranking them out or not producing them. The 116 recorded hybrid k=3 misses share a mean of **4.0 population names with ≥2 tokens in common**
(median 2) — crowding is real but it is *not* predominantly a token-overlap effect: 48 of the 116 misses are in the
identifier class, where the loss is not near-synonym competition.

---

## 4. Arm 2 — a VARIANT mention, bound by the PHASE-1 matcher only (matching)

The node exists; a later capture says it a different, real way. ⛔ **This arm measures `_find_existing_entity`,
phase 1 only.** Production also runs the phase-2 LLM over these same candidates (§1 consequence 3); that arm is
**not measured here** and §7 names it as the next work.

**Variant rules (declared, in `variants()`):** one pair per name per applicable rule — article-drop for every
`the …` name, **hyphen→space for every name carrying a hyphen**, **space→hyphen only for a `definite` name** (the
class gated at `variants()`), basename-only for every path,
`#`-drop for every `#n`. ⚠️ That gate is inert on today's population — the harvest regexes for the `ident` and
`path` classes admit no spaces — so the 193 separator pairs are unchanged; it is stated because a reader
deriving the rules from this sentence must not implement a rule set the harness does not run. ⛔ **The class mix is
the harness's, not an observed incidence.** The transformations are
ones `_norm` provably does not fold, but nothing here measures how often a capture actually emits each shape — so a
share of the 419 pairs (e.g. *"27.2 % of all variants"*) is a share of **these rules' output**, never of duplicate
captures in production traffic. The 193 separator pairs illustrate it exactly: they are the rule firing on every
name that contains a separator, not 193 observed re-spellings.

| k | pairs | canonical **in** window | **phase-1 attached** | attached under a candidate punctuation fold |
|---|---|---|---|---|
| **3** | 419 | 260 = 62.1 % | **0 = 0.0 %** | 114 = 27.2 % |
| 5 | 419 | 267 = 63.7 % | **0 = 0.0 %** | 115 = 27.4 % |
| 10 | 419 | 273 = 65.2 % | **0 = 0.0 %** | 116 = 27.7 % |
| 15 | 419 | 275 = 65.6 % | **0 = 0.0 %** | 117 = 27.9 % |

The keyword-only control gives the same qualitative result — in-window 202 (48.2 %) → 247 (58.9 %) across k=3 → 15,
**phase-1 attached 0.0 % at every k**, folded 53 (12.6 %) → 62 (14.8 %) — so **the phase-1 conclusion is
mode-independent**, which is what makes it safe to act on.

**Decomposed** (hybrid, k=3 → k=15):

| variant class | pairs | in window @3→@15 | phase-1 attached | attached under punctuation fold |
|---|---|---|---|---|
| article dropped (`the plan doc` → `plan doc`) | 179 | 108 → 111 | **0 → 0** | **0 → 0** |
| separator (`the admin-merge rail` ⇄ `the admin merge rail`) | 193 | 114 → 117 | **0 → 0** | 114 → 117 |
| path basename only | 30 | 21 → 30 | 0 → 0 | 0 → 0 |
| `#` dropped (`#11901` → `11901`) | 17 | 17 → 17 | 0 → 0 | 0 → 0 |

**What this says.** Widening the window raises the share of variants whose canonical is *present* (62.1 % → 65.6 %)
and changes phase 1's attach rate by **exactly zero**; every in-window pair is discarded by the matcher. `limit` is
not phase 1's constraint at any width — the normalisation is. A punctuation fold recovers **100 % of the in-window
separator variants** (114/114 at k=3, 117/117 at k=15) and **0 %** of the article class: an article is not
punctuation, and that rung is phase 2's or the embedding leg's job by design.

---

## 5. The duplicate rate, phase-1 only, in the configurations it was measured in

On the same 419 variant pairs:

| configuration | phase-1 attaches | reaches phase 2 with the right candidate |
|---|---|---|
| **prior off** (no link-before-create) | 0 % | n/a |
| **prior on, `limit=3`, phase 1** | 0.0 % | 62.1 % |
| **prior on, `limit=15`, phase 1** | 0.0 % | 65.6 % |
| prior on, `limit=3`, phase 1 + punctuation fold *(NOT shipped)* | 27.2 % | 62.1 % |
| prior on, `limit=15`, phase 1 + punctuation fold *(NOT shipped)* | 27.9 % | 65.6 % |

⚠️ **Read the last column before drawing a production conclusion.** A variant that reaches phase 2 *is* inside the
candidate table the LLM is told to resolve aliases from — so "phase 1 attaches 0.0 %" is **not** "this capture
mints a duplicate". What is measured is that **the window's width decides how often phase 2 even gets the chance**
and that **phase 1 never takes it**; whether phase 2 takes it is unmeasured, and it is the largest single unknown
in this document.

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
| **Zep/Graphiti** searches **up to 15 candidates** at cosine **≥ 0.6**, and **the threshold only generates candidates — it does not decide**; a deterministic similarity resolver or an **LLM** chooses the existing node or declares a new one | arXiv 2501.13956 (`arxiv.org/html/2501.13956`); `graphiti_core/utils/maintenance/node_operations.py` | No | **ADOPT the shape, not the number.** The generate-then-decide split is already ours (phase 1 → phase 2). Copying "15" is **counter-indicated by measurement**: our per-query width is 3 but the candidate list is ~40, so "15" is a *narrowing*. |
| **Mem0** is ADD-only: no similarity cutoff merges memories; an extracted entity links to an existing one by **exact name or cosine ≥ 0.95**, and that is a *link*, not a merge | `docs.mem0.ai/core-concepts/how-it-works.md`, `/memory-operations/add` | No | **NOT ADOPTED as a threshold.** 0.95 would be a **second constant** beside `tortoise.embeddings.DEFAULT_THRESHOLD = 0.72` (`tortoise/embeddings.py:763`), and *"a second constant for a similarity threshold"* is a **REFUSED** row in research-brief §5. The *shape* (link, never merge) is ours already. |
| **pgvector filtered ANN**: a `WHERE` predicate is applied **after** the ANN scan; a selective predicate over-filters and returns fewer than `LIMIT` or misses true neighbours; `hnsw.iterative_scan` (≥ 0.8) keeps scanning and **does not** turn it into exact filtered NN | `docs.pgedge.com/pgvector/v0-8-7/{filtering,iterative-index-scans}`, supabase HNSW guide | No | **NOT THIS STEP, and it binds step 2.** Irrelevant at the measured population: a scan is exact and an approximate index is parameters + rebuild + lost exactness for nothing (§12.1a). It stays the named risk **when** the index is built. |
| **LongMemEval** publishes no isolated per-component ablation for entity resolution or write-time dedup; its reported gains are in **indexing and query formulation** | arXiv 2410.10813 (`arxiv.org/html/2410.10813`) | No | **CONFIRMS the gap this issue exists to close**: there is no external number to borrow. `docs/benchmarks/longmemeval-500q/run-report.md` stays `PENDING` — **no LongMemEval figure of our own is claimed here.** |
| **Blocking recall degrades with scale and surface variation**: token/lexical blocking falls ~0.91 → ~0.55 as records go 10 K → 2 M (block-size caps); embedding blocking reaches ~89.9 % pair completeness; a single HNSW index measured 97.7 % | arXiv 2202.12521; arXiv 2607.26298; Mannheim SC-Block (ESWC 2024) | No | **ADOPT as the frame, not as a number.** Our 61.3 % hybrid recall@3 is a *different quantity* (the share of queries whose exact node is inside a 3-wide window, not pair completeness), so it is not compared to these as a number; what carries over is the recorded diagnosis (block-size caps / crowding), which matches ours qualitatively; it is *why* "widen `k`" reads as the obvious fix and why the measurement was needed to show it is not. |

**Adoption gate:** `adopt` (the generate-then-decide shape; the blocking-recall frame) · `no recorded decision
reached` for the window size and the verdict step — that is #11901's live question and it is now measured ·
`refuse` (a second similarity constant — Mem0's 0.95; building the index before the resolution step) · `no reopen`
— nothing here contradicts (1), (2) or (3).

---

## 7. The decision the evidence supports

**1. Do not widen `limit` on its own. The prior should not change yet on a window argument.**
It recovers 30.2 % of the hybrid misses (61.3 % → 73.0 %), moves the **419-pair variant arm's** phase-1 attach
rate by **0.0 %** (in the *exact* arm `attached` rises 184 → 219 only because a mention that *is* the name
attaches once it is in the window — that is recall@k, not matching), and takes the already-~40-candidate list to a
**measured 170.8** at `limit=15` — which is research-brief **X7**'s regime, where
selection accuracy falls as the candidate list grows. A wider window **without** a verdict step is the recorded
regression; the measurement says the window is not even the right first lever.

**2. The measured next changes, in this order, and none is shipped here:**

- **(2a) Measure phase 2 before changing anything else.** It is live in production, it is instructed to resolve
  exactly the variant class, and it is handed 62.1 % of those variants at `limit=3`. **This is the biggest
  unknown and it gates (2b) and (2c)**: if phase 2 binds them, the fold buys little; if it does not, that is the
  measured loss.
- **(2b) The phase-1 matcher's normalisation** — a separator fold **in the identity comparison, never in
  `_norm`** (whose other consumers assume its narrow contract): **0 % → 100 %** of in-window separator variants,
  **0 % → 27.2 %** of all variants, and **+0.7 pts** available from widening `k`. ✓ **Mode-independent in the two
  claims that carry the conclusion** — the **0.0 %** attach and the **114/114 · 117/117** recovery hold in BOTH
  modes (§4). The **27.2 %** and **+0.7 pts** are the HYBRID arm: the keyword-only control folds **12.6 % →
  14.8 %** (53/419 → 62/419).
- **(2c) The class split in §3.1** — the dense leg *worsens* the definite-description class by 13.3 points while
  improving identifiers by 25.6 and paths by 63.3. That is a fusion/weighting question (RRF competing
  near-synonyms), not a width question, and it is where §12.2c's *resolution key* claim needs its own
  measurement.
- **(2d) The article class** (179/419) — reached by neither the window nor a punctuation fold. Phase 2 or the
  embedding rung, on the existing `DEFAULT_THRESHOLD = 0.72`, never a new constant.

**3. The index question, answered on the evidence rather than before it: NOT YET.** At the measured population a
scan is exact. The hybrid sweep (300 names × 4 widths × 3 arms plus the harvest) completed in **390 s** against an
**unindexed** label; the keyword-only control took 24 s. §12.1a's size-earned rule, §14.3 V3's sequencing and
pgvector's post-filter trap all point the same way: resolution step first, index when volume earns it.

---

## 8. What this document does NOT claim

- **Not a production census.** The population is a declared sample of real capture strings (§2).
- **Not a class-share estimate.** The quotas oversample identifiers and paths on purpose (§2).
- **Not a production duplicate rate.** §5 is phase-1 only; §4's "0.0 %" is not "this capture mints a duplicate".
- **Not observed variants.** §4's 419 pairs are **GENERATED** from harvested names by the harness's rewrite rules
  (`variants()`: article-drop, separator swap, basename-only, hash-dropped). The base names are real; the variants
  are constructed, so "0.0 % attached" is a property of the construction as well as of the matcher.
- **Not an observed variant mix.** The 419 pairs are generated by the declared rules in `variants()` (§4); only the
  *names* they are derived from are real, so a share of them is a property of the rules.
- **Not a measurement of phase 2.** It is live in production and unmeasured here (§1, §7-2a).
- **No mechanism for the identifier class's keyword-only failure.** The keyword-only control returns **zero rows
  for 11/300 names**, 8 of them identifiers; the dense leg removes that entirely (`rows_zero` 0 in hybrid). That
  is two measurements, not a diagnosis — and the class is in §7-2c, not closed here.
- **No LongMemEval number.** `run-report.md` stays `PENDING`.
- **No claim that this window is the product's main retrieval cost.** This is the *capture* path only.

## 9. Reproduce

```bash
# 1. a lane container, with its four seams exported
uri="$(uv run python tools/test_lane.py uri)" || exit 1
eval "$uri"
# 2. the embeddings extra — WITHOUT it the fused read degrades to keyword-only
#    and you are measuring the lexical leg (still a valid artifact: the tool
#    records the mode in `retrieval`)
uv sync --extra embeddings --extra parity
# 3. the measurement  (NOT via `test_lane.py run`, which executes pytest)
uv run python tools/candidate_recall_11901.py \
    --population 1000 --sample 300 --captures 20 --out /tmp/measurement.json
```

`tests/test_candidate_recall_11901.py` pins the structural facts this document rests on — the union across derived
queries **in `extractor_v2.search_graph` itself**, driven with a stub client (a test over the artifact cannot see the
production mechanism change, which is why the first version of this sentence was false); the exact phase-1 matcher;
the variant classes, that both committed artifacts are internally consistent
and declare their retrieval mode, and that the tables above match the artifact — so the numbers cannot rot away
from the evidence.

⚠️ **Run them the way the repo requires** — the module needs no *graph*, but every `pytest` run in this repo
needs the lane's DB seam (epic #1647 P4), so a bare `uv run pytest tests/test_candidate_recall_11901.py` reports
**errors** from `tests/_embedded.py` (one per collected test — 22 of them at this head), not a failure of these
tests:

```bash
uv run python tools/test_lane.py run -- tests/test_candidate_recall_11901.py -q   # every test passed
# …or, for the URI-less embedded carve-out:
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_candidate_recall_11901.py -q
```

**What is pinned, precisely** (because "the tables match" was untrue of §5 until round 5): every measured row of §1,
§3, §3.1, §4 (both the arm table and the decomposition) and §5 (§5's first row is the DEFINITIONAL baseline — no
harness has a prior-off arm — and is pinned as text); §3's prose derivatives (past-this-k, absent-at-15, the
identifier share, the crowding mean and median); §6's and §7's restatements (the recall pair, the pooled mean, the
fold's two denominators, the width residual, the class deltas, the wall clock); §2's between-run drift (the
`corpus.harvested_distinct` pair and the percentage between them); §3's keyword-only miss split and k=5 mean rank;
and §8's `rows_zero` claim. Each
§3 row is anchored to **its own** section — a swap of the two tables' bodies reds, which it did not before review
round 8. **Not
pinned:** §2's corpus census (file count, record count, characters — the artifact carries only the first, and the
corpus is live), §2.2's graph/digest, and `retrieval.window_modes` / `retrieval.uniform_windows`, which the committed
artifacts predate. Three unpinned sentences are the
honest state; an unpinned TABLE is not.

## 10. Write-back

**`wrote:`** not written — the local graph this issue names is absent on this box (nothing listens on 16379; all
six cited ids answer GRAPH-EMPTY-RESULT), and the hosted graph REFUSED the write: `tortoise-graph.sh evidence …`
→ `{"error":"Team points limit reached (25000). Upgrade your plan to increase it.","code":-32006}`.

**`edges:`** none — no hosted point on this topic exists to attach to (the topic lives on the absent local graph),
so an edge would have been fabricated. The finding lives in this document, in the two artifacts, and on #11901; the
point that would have carried it is blocked on a quota, not on a decision.
