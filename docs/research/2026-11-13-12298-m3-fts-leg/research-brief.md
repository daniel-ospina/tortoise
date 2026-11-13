---
title: "M3 — the FTS leg's recall@k in isolation: BM25-in-Postgres vs the in-graph TF-IDF leg, and what the gap actually is (#12298)"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-13
updated: 2026-11-13
subjects.team: epistemic-team
issue: "#12298"
aboutSubjects: text ranking, FalkorDB fulltext index, ParadeDB pg_search, sparse query tokenizer, capture-time noun prior
aboutObjects: SEARCH-ARCHITECTURE.md §6.2 / measure M3, epic #12186 C2 + C3, #11901 candidate-recall instrument, STORAGE-ARCHITECTURE.md §12.1/§12.2c, #11395 KEEP ruling
---

# M3 — the FTS leg's recall@k in isolation

**Issue:** [#12298](https://github.com/daniel-ospina/tortoise/issues/12298) · **Decision this feeds:**
`SEARCH-ARCHITECTURE.md` §6.2 (the text index does not move) and epic `#12186` **C2** (BM25 instead of
TF-IDF) + **C3** (SPLADE). · **Harness:** `tools/m3_fts_leg_recall.py` · **Artifacts, this directory:**
`population.json` (the pinned, paired sample), `artifact.json` (every arm + the paired tests), and
`artifact-repeat.json` (a **second independent run**, to evidence repeatability — its recall tables and
verdict are byte-identical to the first) · **Measured:** 2026-11-13, 300 names on one box, both backends
in one process, twice.

---

## 0. Verdict, and the two readings of the falsifier

**The scorer is not the constraint, and moving the text index buys no recall. The 25-point difference
that made the move look valuable is a tokenizer contract defect in our own query builder, and it is
reproducible in the index we already run.**

| arm | recall@3 | Δ vs shipped | paired p | what it is |
|---|---|---|---|---|
| `falkordb_tfidf` (shipped) | **0.7133** | — | — | `db.idx.fulltext.queryNodes`, RediSearch TF-IDF, index created with no `OPTIONS` |
| `postgres_bm25/tokens` | **0.7267** | **+1.33 pts** | **0.344** | ParadeDB `pg_search` BM25, our production term set |
| ⤷ the same, **tokens-controlled subset** (n=174) | 0.5287 vs 0.5172 | **+1.15 pts** | **0.727** | the control is only real where a term list exists (§2) |
| `postgres_bm25/native` (deployable) | 0.9633 | +25.00 pts | <0.001 | ParadeDB BM25, its own analyser |
| `falkordb_tfidf__tokens_fixed` (in place, NOT shipped) | **0.9633** | **+25.00 pts** | <0.001, **0 regressions** | **the shipped index**, `_`-preserving runs added as OR alternatives |

**Reading 1 — the issue's falsifier, taken literally.** `#12298` says: *"If BM25-in-Postgres does not
beat the current scorer on the FTS leg's recall@k, the leg is closed and no SPLADE work happens."* A
deployable BM25-in-Postgres **does** beat it: **0.9633 vs 0.7133, +25.00 pts, p < 1e-6**. On that
sentence alone M3 **passes**, and the instruction that follows is *"proceed straight into SPLADE"*.

**Reading 2 — what the measurement decomposes to.** The +25.00 pts is not BM25:

* with the query terms held constant, BM25 buys **+1.33 pts** (p = 0.34) overall, **+1.15 pts**
  (p = 0.73) on the subset where the control holds, and **+0.00 pts at k=15 with ZERO discordant
  pairs**. ⚠️ Zero *discordance* is about the **window**, not the **order**: of the 228 names both arms
  find inside k=15, **45 rank differently** (mean rank when found 1.452 vs 1.285, largest move 5 places —
  `m3-000014` goes 1 → 6). What the control rules out is BM25 buying *recall*; it does not say the two
  scorers order documents the same way, and an earlier cut of this sentence did;
* the same **+25.00 pts is recovered inside the current index** (0.9633, **zero** regressions), by
  adding the `_`-preserving runs of the query as OR alternatives — the `#8933` additive pattern;
* the cause is measured and is ours: **RediSearch treats `_` as a word character; `sparse.py` splits on
  it** (§3.4). A stored `web_search` is queried as `search|web` — two terms the index never holds.

**This lane recommends Reading 2, and says so openly — it does not act on it.** The recommendation's
ground is that the owner's pre-authorisation was conditional on a measurement whose *premise* was that
the text index's **ranking** limits recall. That premise is now measured and false, so building a
learned-sparse representation — which re-indexes a vocabulary of its own and would therefore **mask** the
defect (§6) at a neural pass per query — would be buying a lever for a cause that no longer exists. A
recommendation is not a ruling, and §0 goes on to say why this lane cannot make one.

⛔ **But closing C3 is not this lane's call, and this brief does not make it.** `#12298`'s step 3 is a
recorded owner instruction — *"If M3 passes — proceed straight into SPLADE / learned sparse. Do **not**
re-litigate, do not split it out, do not wait for another approval. The owner pre-authorised this on
2026-11-12."* — and the literal falsifier it states is **not** met: a deployable BM25-in-Postgres does
beat the current scorer (Reading 1). A lane may not decline pre-authorised scope on its own reading any
more than it may adopt something over a decision: both are reversals of the same ruling, and the route
in either direction is a **reopen argued in front of the owner**, in this issue's own home. So what this
brief records is the **measurement, plus a recommendation** — that C3's premise is falsified and the
learned-sparse lever should be reconsidered — and it leaves the ruling open for the owner. ⛔ **`#12298` is CLOSED** (`COMPLETED`, 2026-11-13T05:21:53Z) on the reading *"M3 FAILS"*, which this measurement contradicts: the closing condition in its own falsifier sentence is **not triggered** — a deployable BM25-in-Postgres beats the current scorer. The C3 ruling therefore lives in the OPEN homes — epic `#12186` and the pre-registration issue `#12192` — not in `#12298`, which records a closure this PR's numbers do not support and which needs the owner's correction rather than a lane's.
**Until that ruling, C3 is neither closed nor proceeding.** Everything downstream of the deferral
(§6.2's decision, V1, `#12186`) therefore keeps its existing status rather than being re-stated here;
the evidence for the reopen is in §0 and §3.

**Therefore:** the text index **does not move** on this evidence — that is the measurement's answer to
§6.2's M3 question, not a new decision. **No `OVERRIDES` line is minted, because nothing is adopted
over a recorded decision** — and nothing is declined over one either: the C3 ruling is left where it
lives.

---

## 1. Preflight — what could and could not be used

**Search tool.** Rung 1 (`mcp__seo-intelligence__perplexity_search`) **could not be loaded**:
`mcp_load seo-intelligence` → `spawn /mcp-servers/seo-intelligence/node_modules/.bin/tsx ENOENT` — the
same failure `#11901` recorded, still unfixed. Rung 2 (`web_search`) refused with
`PERPLEXITY_API_KEY not set`: the key exists in `~/keys.env` (mode 0600) but not in this shell's
environment, so the queries were issued **against the Perplexity API directly with the model named
explicitly on every request** (`"model": "sonar"`; no `sonar-deep-research`, no `sonar-reasoning-pro`).
**Queries: 4 attempted / 3 returned** (one `429`, retried). The rung taken is **rung 2, direct**.

**Graph.** Not consulted: this lane's dispatch names
`~/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md`, which **does not exist on this box** (the dispatch's own
mandate-preflight says so; agent-infra#3377). The internal record used instead is the documents and
`#12186`'s three research rounds, read in full.

**What was actually deployed, and why that version.** ParadeDB `pg_search` **0.26.6** on **PostgreSQL
16.15** (`paradedb/paradedb:0.26.6-pg16`) — the same Postgres version the record substrate targets
(`supabase/migrations/20261109000001_record_embedding_vector.sql` cites "pgvector 0.8.7 / Postgres
16.15"). ParadeDB is the only option the research pass found that is a documented native BM25 index with
a Docker deployment path for PG 16; Supabase's **hosted** platform does not offer it (self-managed only),
which is recorded here because it constrains where the option could ever run. Timescale `pg_textsearch`
and VectorChord-BM25 were named as candidates; neither had a verifiable PG-16 artifact in the sources
read, and one working option is what M3 requires.

**The pinned read of the code** (head `c17e3c5f78`, locators named because §6.2's own have drifted twice):

| what | where | what it decides |
|---|---|---|
| the query builder | `tortoise/sparse.py:505` `build_or_query` | OR-union of `tokenize_sparse_query` terms, then the #8933 alias/phrase tail, deduped against the originals |
| the tokenizer | `tortoise/sparse.py:381` `tokenize_sparse_query` | splits on **every** `[^a-z0-9]` (`_SPLIT_RE` at `:58`) — **including `_`** |
| the query term cap | `tortoise/sparse.py:63` `DEFAULT_MAX_OR_TERMS = 12` | 12 terms, longest-first |
| the degenerate branch | `sparse.py:505` — `return escape_redisearch_literal(query)` | when ≤1 token survives, the whole query is the **escaped raw string** |
| the leg | `tortoise/search_engine.py:2800` `run_fts_query`, Cypher at `:2986-2998` | `CALL db.idx.fulltext.queryNodes('Object', $query) … ORDER BY score DESC, <recency tie> LIMIT` |
| the tie key | `search_engine.py:2642` `recency_tie_clause` | `coalesce(<recency>,'') DESC, coalesce(<id>,'') DESC` — the **id key is DESC on every label** |
| the score discard | `tortoise/search_engine.py:4492` `for rank, (pid, _score) in enumerate(ranked)` | **why this is a recall question** |
| the index | `tortoise/projection/__init__.py:20333` `("Object", "name", "FULLTEXT")`, created by `_create_fulltext_index` (def at `:20761`, called from `:20747`) | **no `OPTIONS`** → RediSearch defaults (language `english`, default stopwords, default tokenizer) |

---

## 2. Method — paired, pinned, controlled, and where the control breaks

**Why recall and not ranking.** `rrf_fusion` discards each leg's score (`search_engine.py:4492`), so on
the multi-leg read the scorer can only act through `ORDER BY … LIMIT`; the one arm where the magnitude
survives uses it as a *floor* (`SEARCH-ARCHITECTURE.md` §6.2 says the same). A different scorer is
therefore a **recall** lever (`#12186` round 2; `#12308` A-3). So M3 reports `recall@k` of the FTS leg
**in isolation**.

**The population is `#11901`'s, imported.** `harvest()`, `build_population()`, `_allocate_shares()` and
`_interleave()` come from `tools/candidate_recall_11901.py`, so the two measurements cannot drift apart:
1,000 names from this box's real capture corpus (`~/.tortoise/session-events`, **10 files, 8,653 lines,
0 parse errors, 0 records without text**), stratified to a 300-name sample (180 `definite` / 90 `ident` /
30 `path`) and **pinned to `population.json`** and read by both arms in one process with the same ids.

⛔ **That pin closes a gap `#11901` had to declare**: its two artifacts were **between-run** (harvests
427 s apart, and a later re-harvest returned ~11 % fewer names), so its class deltas could only be called
*directional*. M3's comparisons are **paired**, tested on the **discordant pairs only** (exact two-sided
McNemar), and **repeated**: `artifact-repeat.json` is a second independent run whose recall tables and
verdict are identical.

⚠️ **The population is a sample of the strings captures contain, not a census of `:Object` rows**, and
its class quotas are a deliberate **oversample** (ident 30 %, path 10 %) — the composite headline is
**stratified, never class-share weighted** (§4.3 does the weighting separately, with its assumptions
stated).

**The arms.** `falkordb_tfidf` writes the population through the **real projection** (`_upsert_object`,
id-keyed per `#3590`) and calls **`run_fts_query` itself** — no fusion, no dense leg. `postgres_bm25`
writes the same ids/names into Postgres 16 with a `USING bm25 (id, name)` index, runs `ANALYZE` (IDF needs
document frequencies) and queries `WHERE name ||| <terms> ORDER BY paradedb.score(id) DESC, id DESC`.

**The tie rule is part of the measurement** (`#12186` round 3, requirement 6). The shipped leg reads
through `recency_tie_clause`, whose id key is **DESC on every label**; the Postgres arm therefore orders
`id DESC` too. The first version of this harness ordered `id` **ASC** — the opposite direction — and it
moved the deployable arm from **0.9700 to 0.9633** at k=3, which is exactly the boundary effect the
requirement exists to prevent. ⚠️ Declared residual: the *recency* half is controlled on **one side
only**. The arm writes through the real projection, so every `:Object` row does carry `createdAt`
(`tortoise/projection/entities.py` — `o.createdAt=coalesce($ca, $now)` on create) and the shipped leg's
recency key is **live**; the Postgres arm has no timestamp column at all. The two orders agree here
**only because the sweep writes in ascending id order**, which makes `createdAt DESC` emit the same
sequence as `id DESC` — a coincidence of write order, not a controlled match, and the one place where a
change to the write loop would diverge the tie orders with nothing in this lane's numbers to show it. An
earlier cut of this paragraph said no comparable timestamp existed and that the key collapsed to the id
key on both sides; that was wrong about the shipped side and is corrected here.

**Where the control breaks — stated, not hidden.** C2 is *"BM25 instead of TF-IDF"*, a **scorer**
change, so the primary arm hands Postgres **our production term set** rather than its own analyser's. But
`build_or_query` has a **degenerate branch**: when ≤1 token survives tokenization it returns the
**escaped raw string**, and there is then no term list of ours to hand over. On those queries this
harness sends the raw sanitised text to Postgres — which is verbatim the `native` arm.

**126 of the 300 queries are degenerate** (108 `definite` — a `the X` name whose only surviving token is
`X` — and 18 ident/path), and for every one of them the two Postgres arms' ranks are identical. The
artifact records the split (`terms_census`, `degenerate_ids`) and a second comparison restricted to the
**174 tokens-controlled queries**, which is the number quoted for the primary arm in §0:
**0.5172 → 0.5287, +1.15 pts, p = 0.727**. (Of the full arm's net +4 rows at k=3, **2** sit on collapsed
rows.) Inventing a term list the shipped leg never sends would have been a different measurement, not a
fairer one — so the break is disclosed instead.

**Two harness hazards were measured out, not assumed.**
(a) **The index builds on a background cycle.** `db.indexes()` reports `numDocuments`; a query issued
straight after the writes can see a **partially built** index and book a present name as a miss — an
artefact in the direction that flatters the wrong arm. The harness **waits** until `numDocuments`
reaches the population size and **refuses** on timeout; it also **refuses a graph whose stored names do
not match the pinned population**, because `o.name` is an ON CREATE write (`#8919`) — a second run
against the same graph would measure the previous population, and the count guard alone cannot see that.
⚠️ Both guards are narrower than "refuses any reused graph": the wait accepts `numDocuments >= n` (an
over-full index passes it), and the name check compares the pinned names — so
`--graph <a previous run's graph>` with the SAME population is ACCEPTED, and the shipped sweep then runs
over its leftover fixture rows. That is the confound §3.5 and `measurement_notes` disclose; passing no
`--graph` (the documented reproduction) is what avoids it.
(b) **Re-upserting into a live graph is not free** — before the wait and run-scoped graphs existed, two
runs differed by 2 names (0.7133 vs 0.7067, an uncommitted pre-run observation). With both, two
independent runs return **identical** numbers, and the second one is committed as
`artifact-repeat.json`.

**Requirement 4 (the single-leg floor arm)** is recorded in the artifact's `threshold_arm`: **no
`threshold` is passed to either arm**, because the harness calls the FTS leg directly and never enters
the SDK's single-leg arm where the floor lives. The shipped default is `0.0` (filter off), so on this
axis both arms are the shipped configuration — and the floor arm is therefore **not exercised**.

---

## 3. Results

### 3.1 Recall@k, all arms (n = 300, paired)

| k | `falkordb_tfidf` | `postgres_bm25/tokens` | `postgres_bm25/native` | `falkordb__tokens_fixed` |
|---|---|---|---|---|
| **3** | **0.7133** | **0.7267** | **0.9633** | **0.9633** |
| 5 | 0.7433 | 0.7367 | 0.9833 | 0.9967 |
| 10 | 0.7567 | 0.7567 | 1.0000 | 1.0000 |
| 15 | 0.7600 | 0.7600 | 1.0000 | 1.0000 |
| empty result sets | **21** | **27** | 0 | **0** |
| ms per query (CPU, this box) | 1.60 | 1.18 | 1.21 | 1.59 |

### 3.2 The paired tests (discordant pairs only)

| comparison | k | both | shipped only | other only | Δ pts | exact two-sided p |
|---|---|---|---|---|---|---|
| shipped **vs** `pg/tokens` | 3 | 211 | 3 | 7 | +1.33 | 0.344 |
| shipped **vs** `pg/tokens`, **tokens-controlled subset** (n=174) | 3 | 87 | 3 | 5 | **+1.15** | **0.727** |
| shipped **vs** `pg/tokens` | 15 | 228 | 0 | 0 | **0.00** | 1.000 |
| shipped **vs** `pg/native` | 3 | 210 | 4 | 79 | +25.00 | <0.001 |
| shipped **vs** in-place fix | 3 | 214 | **0** | 75 | +25.00 | <0.001 |

**Read the `shipped only` column.** The in-place fix has **zero** regressions at k=3 — it is a strict
superset, which is what an *additive* OR term is supposed to be. `pg/native` loses 4 names the shipped
leg found.

### 3.3 Per class (recall@3 / recall@15) — where the whole effect lives

| class | n | shipped | `pg/tokens` | `pg/native` | in-place fix |
|---|---|---|---|---|---|
| definite description | 180 | 0.9778 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 0.9778 / 1.000 |
| identifier (`#n`, `snake_case`) | 90 | **0.2000 / 0.2000** | **0.2000 / 0.2000** | 1.000 / 1.000 | 0.9444 / 1.000 |
| path | 30 | 0.6667 / 1.000 | 0.6667 / 1.000 | **0.6333** / 1.000 | **0.9333** / 1.000 |

**The scorer is not what moves the identifier class** — a *BM25* index fed our terms scores **exactly
what the shipped leg scores** there (0.2000). And on paths the **in-place fix beats the deployable BM25
option by 30 points** (0.9333 vs 0.6333): the migration is not uniformly better even where it does move.

**The identifier class decomposes, and it matters for §4.3:** of its 90 names, the 17 `#n` shapes
(`#2258`, `#3484`) are found by the **shipped** leg **17 of 17** — they carry no `_` and are
unaffected. The movement is entirely the **73 `snake_case` names: 1 of 73 (0.014) shipped → 68 of 73
(0.932) with the in-place fix, +91.8 pts**.

### 3.4 The mechanism — a tokenizer contract mismatch, measured

RediSearch treats `_` as a **word character**. `tortoise/sparse.py:58` does not. Probe on the lane graph
(v4.20.6), verbatim:

```text
'web_search'                -> [['web_search', 8.0]]        # the index's term IS the composite
'web'                       -> []                          # our first OR term: not a term the index holds
'selection'                 -> []
'test_ci_selection'         -> [['tests/test_ci_selection.py', 14.0], ['test_ci_selection', 14.0]]
'capstone_verdict_check'    -> [['capstone_verdict_check', 8.0]]
'test_7949_capstone_verdict_check' -> []                    # absent from THIS probe graph (see §3.5)
```

so for a stored `web_search` the shipped leg issues `build_or_query('web_search')` = `search|web`. The
query is well-formed, the leg returns nothing, and the identifier class's `recall@3` is **0.20**.
**21** of the **300** population queries return an empty result set at k=3 — a whole-population count
(the artifact records `rows_zero` per k, not per class), so it is not a claim about the class: most of
that class's 72 misses at k=3 come back non-empty and simply omit the target.

### 3.5 The `#8933` identifier fixture — requirement 5, and the shipped leg FAILS it

Five fixture records are queried at the production `limit=3` and at k=15, and are inserted **after that
arm's own** recall sweep so they cannot move a document frequency under it. The criterion is
**reachability** (`rank_wide is not None`) — the DoD question is *can this store retrieve a record whose
name carries a composite identifier*, and a fixture row inserted beside 300 look-alike names also
competes on ranking, which `#11901` already owns.

⛔ **Recorded, not tidied:** in the committed artifacts the ordering did NOT hold for the recovery arm.
Both FalkorDB arms shared one graph and the shipped arm inserts its fixtures at the end of its own call,
so the recovery arm's sweep ran over a **305**-document index against the shipped arm's **300** — the
artifact's own `index_numDocuments` records both, unedited. The harness now gives each arm its own graph
(a re-run measures 300 on both), and the direction is not rescued by the defect: five extra documents
compete for the same three slots, so the recovery arm faced five more competitors — plausibly the
harder population, not provably so (five extra documents also move document frequencies, and TF-IDF
re-weighting can move a name up as well as down); the zero-regression result itself does not depend on
that argument, but nothing here should be read as having PROVEN the direction. The re-run is the
confirmation, and it is what `#12350` should be measured with. See `artifact.json`'s `measurement_notes`.

| stored name | query | shipped | in-place fix | `pg/tokens` | `pg/native` |
|---|---|---|---|---|---|
| `ERR-4032` | `ERR-4032` | **2** | **2** | **1** | **1** |
| `INV-2024-0091` | `INV-2024-0091` | **1** | **1** | **1** | **1** |
| `abc_def` | `abc_def` | **never** | **1** | **never** | **1** |
| `test_7949_capstone_verdict_check` | itself | **never** | **4** (k=15) | **never** | **1** |
| `ERR-4032 retry` | `ERR-4032` | **1** | **1** | **2** | **2** |

**Shipped: 3 of 5 reachable. In-place fix: 5 of 5. `pg/tokens`: 3 of 5. `pg/native`: 5 of 5.**

Two of those are decisive and none of them is about the scorer:

* **`abc_def` is unretrievable at ANY k on the shipped leg** — the row for its own name is **absent at the production
  window and at k=15**. ⛔ Stated as what the artifact records (`rank` and
  `rank_wide`, both `None`), not as a row COUNT: the harness does not record the size of an empty
  result set, so "zero rows" was a stronger claim than the committed evidence carries.
  (`abc` and `def` are two terms the index does not hold; it holds `abc_def`). The `#8933` machinery does not help: its alias tail
  (`err4032`) and adjacency phrase (`"err 4032"`) are *whitespace/punctuation* composites, and the index
  does not hold `abc_def` as `abc def` either.
* **The 4-segment run needs the widened criterion** — the in-place fix reaches it at k=15 (rank 4) among
  look-alike population names, the shipped leg never reaches it. This is the one fixture row where the
  pass/fail criterion matters, which is why it is stated and both ranks are in the artifact.

**This is the strongest single result in the lane**: the identifier capability the product believes it
has (`#8933`'s `ERR-4032 → err4032` plus the adjacency phrase) is **defeated by the `_` split** on
exactly the shapes it was built for, and neither a new scorer nor a new store is what restores it.

---

## 4. The verdict, against the rule the harness fixes

### 4.1 The rule, and what "pre-registered" can and cannot prove

> **PASS iff** on the primary arm (`postgres_bm25/tokens`) recall@3 is strictly greater than
> `falkordb_tfidf` recall@3, **AND** the exact two-sided McNemar test over the discordant pairs has
> p < 0.05. **FAIL otherwise** — including a tie, and including a gain carried by pairs where the two
> arms agree.

The rule is the harness's `DECISION_RULE` constant (the symbol, not a line number — locators in this
document rot on every merge, which §1 says out loud) and the artifact
carries it verbatim, so it cannot be re-chosen per run without editing two places — the test asserts that
byte-equality rather than the label. ⚠️ **What that does not prove:** the harness, the artifact and this
brief landed in **one commit**, so the *ordering* — that the constant existed before the 300-name run —
is not checkable from this repository. The repo's own executable form of that claim
(`tools/capstone_preregistration_check.py`) requires an evidence log that **predates** the registration,
and this lane has none. Stated rather than implied: the rule is fixed and carried; its chronology is the
lane's assertion.

**Result: FAIL** — on the full arm (+1.33 pts, p = 0.344) and on the tokens-controlled subset (+1.15 pts,
p = 0.727).

⛔ **This is the harness's rule, and it is narrower than `#12298`'s own falsifier sentence.** §0 states
both readings; the reason the narrower one decides is §4.2, not the word FAIL.

### 4.2 Why the deployable arm does not overturn it

The falsifier asks whether BM25-in-Postgres beats the current scorer on the leg's recall@k. It does
(0.9633 vs 0.7133) — but the **decomposition** is:

* moving to BM25 with the same terms: **+1.33 pts (p = 0.34)**, **+1.15 (p = 0.73)** controlled, **+0.00
  at k=15 with zero discordant pairs** → the scorer contributes nothing measurable;
* recovering that same **+25.00 pts inside the current index**: **p < 0.001, zero regressions**.

An index migration whose entire measured benefit is reproducible in place is an infrastructure cost with
no recall return — and V1 deferred it behind exactly this measurement. The deferral **stands**.

### 4.3 What this is worth at production class shares (and why it is not 25 points)

The sample oversamples identifiers and paths on purpose, so the composite `+25.00` is **not** a
production figure, and the weighting has to respect the decomposition in §3.3:

| class / sub-class | share of real `:Object` names | Δ recall@3 (in-place fix) | weighted |
|---|---|---|---|
| definite description | **62.3 %** (research-brief §3.2, measured) | 0.0 pts (unchanged) | 0.0 |
| `#n` identifiers | **2.2 %** (0.5 % bare `#n` + 1.7 % `PR/issue #n`) | **0.0 pts** (17/17 already found) | 0.0 |
| `snake_case` identifiers | **NOT RECORDED** — inside the audit's 9.0 % "contains a code token" row, which is a superset | **+91.8 pts** | `share × 91.8` |
| path | **NOT RECORDED** (the audit has no path share) | +26.7 pts | excluded, declared |
| everything else | the remainder | assumed 0 | 0, declared |

So the honest statement is: **`share(snake_case) × +91.8 pts`**, with `share(snake_case)` **unmeasured**.
The 9.0 % "contains a code token" share bounds **that one term**: `0.09 × 91.8 ≈ +8.3 pts`. It is **not**
an upper bound on the composite — the path row is a *second* positive term (`share(path) × 26.7`,
excluded only because its share is unrecorded), so any non-zero path share adds on top of it. There is
**no measured lower bound** — the earlier draft of this section multiplied the 2.2 % `#n` share by the
whole class delta and produced a "+1.6 pts floor" from a sub-class whose measured effect is **zero**.
That was wrong and is corrected here. The remainder of the name population — everything outside the
definite, `#n` and code-token rows — is **assumed** Δ = 0; nothing measured it, and the table does not
pin its share either: the rows above sum to 73.5 % (62.3 + 2.2 + 9.0), i.e. **26.5 %**, before any
overlap between the audit's own rows is accounted for. An earlier draft printed "29–38 %" for that
remainder without a derivation; it is removed rather than re-guessed.

### 4.4 Contradiction test — what this does NOT do

| recorded decision | does this contradict it? |
|---|---|
| **V1 / §6.2** — the text index and its payload stay in the graph; the move is deferred behind M3 | **No — this *is* M3**, and its answer is "do not move". The deferral is upheld, not reversed |
| owner's pre-authorised SPLADE reopening (`#12186`, 2026-11-12) | **No adoption is made, so no override is needed.** The authorization was conditional on M3; the measurement falsifies its premise. §0 states the judgement and how to overrule it |
| **KEEP ruling** (`#11395` comment `6781921075`) — `:Object`/`:Subject` keep their stored embeddings | **No.** Nothing here touches an embedding; the measurement is the lexical leg only |
| **§12.2c** — the entity vector is the entry + resolution key, budget-capped | **No.** Not touched |
| **`#11901` conclusion** — do not widen the window before a verdict exists | **No.** `limit` is held at the production 3 (5/10/15 only report the curve); the finding is that **width is not the constraint** either — 21 queries return nothing at any k |

---

## 5. What this does NOT measure (declared, not buried)

* **The fused read.** Every arm measures the **lexical leg in isolation**, which is what M3 asks. Whether
  +25 pts of lexical recall@3 moves the *fused* candidate set the capture prior consumes is
  **unmeasured** — `#11901` showed the dense leg *hurts* the definite class, so the interaction is not
  obviously additive.
* **Production `:Object` rows.** The population is harvested strings, not a graph census; no live
  `:Object` graph is reachable from a lane (`STORAGE-ARCHITECTURE.md` §14.3 records the same limit).
* **The `snake_case` and path class shares.** Neither is recorded anywhere; §4.3 says so rather than
  assuming one, and the residual class share is assumed Δ = 0.
* **Index-time tokenization** is each engine's own and was not controlled (only query-side terms were).
* **The production call shape's terminal-status posture.** The harness asks `run_fts_query(...)` with its
  default `excluded_statuses=None`, so terminal-status Objects are EXCLUDED — but the capture prior's own
  caller (`extractor_v2._fts_rows`) passes `include_terminal=True` for `:Object` (`#3301`: a
  resolution prior, not a surfaced result), which sends `excluded_statuses=()`. On a production graph
  holding terminal Objects the harness therefore measures a **stricter** leg than the prior uses, and
  the measured baseline can understate that caller's recall. Inert here — every population and fixture
  row is freshly written and carries no terminal status — but it is a difference in the shape measured,
  so it is declared rather than left to be discovered.
* **The recency half of the tie rule.** The *id* key is matched (DESC/DESC) and pinned at the harness's
  own SQL. The recency key is **not** controlled: the shipped leg reads a live `createdAt DESC` (the
  projection stamps it on create), the Postgres arm has no timestamp, and the two orders agree **only
  because the sweep writes in ascending id order**. Change that write order and they diverge with
  nothing in this lane's numbers to show it.
* **The single-leg floor arm** (requirement 4) — not exercised; recorded in `threshold_arm` (§2).
* **A SPLADE encoder's cost on CPU.** ⛔ **Not measured, deliberately.** The issue's overhead item sits
  after "if M3 passes"; this lane measured M3 and stopped there (§0 — the C3 ruling is the owner's). The literature range (≈100–300 ms per
  query for a full BERT-base SPLADE encoder on CPU, ≈1–50 ms for distilled variants) is **cited, never
  adopted as ours**.
* **The embedded sklearn TF-IDF fallback** (`tortoise/sparse.py`'s other backend) — outside
  `db.idx.fulltext` entirely, and internally consistent, which is *why* the defect stayed invisible.

## 6. What happens next

1. **`SEARCH-ARCHITECTURE.md` §6.2's DECISION stands; its "unmeasured … until M3" qualifier is now
   resolved and gets a status line.** M3 was the measurement §6.2 asks for; its answer is that the text
   index does not move (§3), so the decision is unchanged — but two sentences in §6.2 still read as
   *outstanding* (`:247` "how far our hosted leg sits from that baseline is *unmeasured* — that is what
   M3's score distributions are for"; `:268` "the text index **stays** until **M3** measures …"), and a
   reader arriving after this PR would be told to go and do work that is done. §6.2 carries a status
   line pointing here rather than a re-statement of the numbers. **C3 (SPLADE) is NOT closed here** —
   that ruling is the owner's and is left open in `#12186` / `#12192` (§0). ⛔ `#12298` was closed `COMPLETED` on 2026-11-13T05:21:53Z — five minutes after this lane's first report and on the reading *"M3 FAILS"*, which the measurement contradicts — so it is NOT the live home of the C3 ruling, and citing it as one (as an earlier revision of this brief, the changelog, `SEARCH-ARCHITECTURE.md` and PR #12349 all did) pointed a reader at a dead record.
2. **The one real lever this measurement found is filed: `#12350`** — the tokenizer contract between
   `sparse.tokenize_sparse_query` and RediSearch's `_`-as-word-character. **Measured, not shipped** here:
   the additive term is an arm of this harness, because a query-bytes change touches every label and
   every caller and narrows `#2070`'s byte-identity control. It needs its own review with this number in
   front of it. **The fixture in §3.5 is the acceptance test it should carry.**
3. `#11901`'s `rows_zero` (11 there, 21 here) and its `21.1 %` keyword-only identifier figure now have a
   cause; the finding is carried there as evidence, not as a peer issue.

## 7. Write-back

**`wrote:`** not written — `~/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md` does not exist on this box (the
dispatch's own mandate-preflight reports it, agent-infra#3377), so the graph write path named by this
repo's skill table cannot be followed from here. The finding lives in this document, in the three
artifacts, and on `#12298` / `#12186` / `#11901` / `#12350`.

**`edges:`** none, for the same reason: with no reachable point on the topic, an edge would be fabricated.
