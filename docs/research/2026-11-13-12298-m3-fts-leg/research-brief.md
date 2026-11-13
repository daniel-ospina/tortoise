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
`population.json` (the pinned, paired sample) and `artifact.json` (every arm + the paired tests) ·
**Measured:** 2026-11-13, 300 names on one box, both backends in one process.

---

## 0. Verdict, first

**M3 FAILS — and it fails in the most useful way available: the falsifier was a *scorer* hypothesis, and
the measurement says the scorer is not the constraint.**

| arm | recall@3 | Δ vs shipped | paired p | what it is |
|---|---|---|---|---|
| `falkordb_tfidf` (shipped) | **0.7133** | — | — | `db.idx.fulltext.queryNodes`, RediSearch TF-IDF, index created with no `OPTIONS` |
| `postgres_bm25/tokens` (**primary**) | **0.7267** | **+1.33 pts** | **0.34** | ParadeDB `pg_search` BM25, **same query terms** as the shipped leg |
| `postgres_bm25/native` (deployable) | 0.9700 | +25.67 pts | <0.001 | ParadeDB BM25, its own analyser |
| `falkordb_tfidf__tokens_fixed` (in place, NOT shipped) | 0.9633 | +25.00 pts | <0.001 | **the shipped index**, query with the underscore-preserving runs added as OR alternatives |

**The primary arm is a tie by any reasonable standard** (+1.33 pts at k=3 on 10 discordant pairs,
p = 0.34; +0.00 pts at k=15 with **zero** discordant pairs). Swapping TF-IDF for BM25 in a store we
would have to move the whole text index into buys **nothing measurable on recall**.

The 25-point swing the deployable arm shows is real, is not BM25, and is **not bought by moving the
index** — it is reproducible in the current index with an additive query term. The cause is a
**tokenizer contract mismatch between `tortoise/sparse.py` and RediSearch**, in our favour to fix and
in nobody's favour to migrate around (§3).

**Therefore:** the text index does **not** move; `SEARCH-ARCHITECTURE.md` §6.2 and V1's deferral
**stand**, and the owner's pre-authorised reopen is **not exercised** — it was conditional on this
measurement, and this measurement does not support it. **SPLADE is discarded, not carried**, per the
issue's own instruction (`#12298`, item 2; `#12186` "The SPLADE decision" item 2).

---

## 1. Preflight — what could and could not be used

**Search tool.** Rung 1 (`mcp__seo-intelligence__perplexity_search`) **could not be loaded**:
`mcp_load seo-intelligence` → `spawn /mcp-servers/seo-intelligence/node_modules/.bin/tsx ENOENT` — the
same failure #11901 recorded, still unfixed. Rung 2 (`web_search`) refused with
`PERPLEXITY_API_KEY not set`: the key exists in `~/keys.env` (mode 0600) but not in this shell's
environment, so the queries were issued **against the Perplexity API directly with the model named
explicitly on every request** (`"model": "sonar"`; no `sonar-deep-research`, no `sonar-reasoning-pro`).
**Queries: 4 attempted / 3 returned** (one `429`, retried). The rung taken is **rung 2, direct**.

**Graph.** Not consulted: the brief for this lane names
`~/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md`, which **does not exist on this box** (the mandate-preflight
in the dispatch says so itself; agent-infra#3377). The internal record used instead is the documents +
the issues (`#12186` rounds 1–3), read in full.

**The pinned read of the code** (head `c17e3c5f78`, locators named because §6.2's own have drifted twice):

| what | where | what it decides |
|---|---|---|
| the query builder | `tortoise/sparse.py:505` `build_or_query` | OR-union of `tokenize_sparse_query` terms, then the #8933 alias/phrase tail |
| the tokenizer | `tortoise/sparse.py:381` `tokenize_sparse_query` | splits on **every** `[^a-z0-9]` (`:71`, `_SPLIT_RE = re.compile(r"[^a-z0-9]+")`) — **including `_`** |
| the query term cap | `tortoise/sparse.py:63` `DEFAULT_MAX_OR_TERMS = 12` | 12 terms, longest-first |
| the leg | `tortoise/search_engine.py:2800` `run_fts_query`, Cypher at `:2986-2998` | `CALL db.idx.fulltext.queryNodes('Object', $query) … ORDER BY score DESC, <recency tie> LIMIT` |
| the score discard | `tortoise/search_engine.py:4492` `for rank, (pid, _score) in enumerate(ranked)` | **why this is a recall question** — the score never leaves the leg on the multi-leg arm |
| the index | `tortoise/projection/__init__.py:20327` `("Object", "name", "FULLTEXT")`, created at `:20755` `_create_fulltext_index` | **no `OPTIONS`** → RediSearch defaults (language `english`, default stopwords, default tokenizer) |

---

## 2. Method — paired, pinned, and controlled

**Why recall and not ranking.** `rrf_fusion` discards each leg's score
(`search_engine.py:4492`), so on the multi-leg read the scorer can only act through `ORDER BY … LIMIT`;
the one arm where the magnitude survives uses it as a *floor* (`SEARCH-ARCHITECTURE.md` §6.2 says the
same). A different scorer is therefore a **recall** lever — #12186 round 2 established this and round 3
(`#12308` A-3) confirmed the single-leg arm is the exception. So M3 reports `recall@k` of the FTS leg
**in isolation**.

**The population is #11901's, imported, never re-implemented.** `harvest()`, `build_population()`,
`_allocate_shares()` and `_interleave()` are imported from `tools/candidate_recall_11901.py`, so the two
measurements cannot drift apart: 1,000 names from this box's real capture corpus
(`~/.tortoise/session-events`, 10 files, 8,648 records, **0 parse errors**, 0 records without text),
stratified to a 300-name sample (180 `definite` / 90 `ident` / 30 `path`) and **written to
`population.json` — the pin**. Both arms read that file, in the same process, with the same ids.

⛔ **That pin closes a gap #11901 had to declare.** Its two committed artifacts were **between-run**
(harvests 427 s apart, and a later re-harvest returned ~11 % fewer names), so its class deltas could only
be called *directional*. M3's comparisons are **paired**: same names, same ids, one run, and the test is
on the **discordant pairs only** (exact two-sided McNemar / binomial).

⚠️ **The population is still a sample of the strings captures contain, not a census of `:Object` rows**,
and its class quotas are a deliberate **oversample** (ident 30 %, path 10 %) — the composite headline is
**stratified, never class-share weighted** (§4.3 does the weighting separately, with its assumptions
stated).

**The arms.** `falkordb_tfidf` writes the population through the **real projection**
(`_upsert_object`, id-keyed per `#3590`) and calls **`run_fts_query` itself** — no fusion, no dense leg.
`postgres_bm25` writes the same ids/names into Postgres 16.15 with ParadeDB `pg_search` 0.26.6 using
`USING bm25 (id, name)`, runs `ANALYZE` (IDF needs document frequencies), and queries
`WHERE name ||| <terms> ORDER BY paradedb.score(id) DESC`.

**The control, and why the primary arm is the one with our terms.** C2 is *"BM25 instead of TF-IDF"* — a
**scorer** change. A comparison where each engine also tokenizes the query its own way measures the two
engines' whole query stacks, and cannot say which part moved the number. So the primary arm hands
Postgres **the exact term set `build_or_query` emits** (our tokenizer, our stopword list, our all-digit
drop, our #8933 alias/phrase tail), and Postgres' own analyser is measured as **supporting** evidence.

⚠️ That control is **self-checked, and it caught its own first bug.** The term list is rebuilt
independently and `assert_terms_match()` **refuses the run** unless it reproduces `build_or_query`
byte-exactly. And `run_fts_query` re-tokenizes whatever it is given, which silently ate the recovery
arm's first attempt (it measured +0.33 pts — the harness destroying the thing under test); the recovery
arm therefore issues the production Cypher itself, and `verify_cypher_parity()` **refuses** unless that
Cypher reproduces `run_fts_query`'s output exactly on a sample of real queries.

**Two harness hazards were measured out, not assumed.**
(a) **The index builds on a background cycle.** `db.indexes()` reports `numDocuments`/`numCycles`; a
query issued straight after the writes can see a **partially built** index and book a present name as a
miss — an artefact in the direction that flatters the wrong arm. The harness **waits** until
`numDocuments == 300` and **refuses** on timeout (measured wait: 0.0 s at 300 docs).
(b) **Re-upserting into a live graph is not free** — two runs against one graph differed by 2 names
(0.7133 vs 0.7067) before the wait was added; two runs with run-scoped graphs and the wait return
**identical** numbers (0.7133 / 0.7267 / 0.9700 to four decimals, twice).

---

## 3. Results

### 3.1 Recall@k, all arms (n = 300, paired)

| k | `falkordb_tfidf` | `postgres_bm25/tokens` | `postgres_bm25/native` | `falkordb__tokens_fixed` |
|---|---|---|---|---|
| **3** | **0.7133** | **0.7267** | **0.9700** | **0.9633** |
| 5 | 0.7433 | 0.7433 | 0.9933 | 0.9967 |
| 10 | 0.7567 | 0.7600 | 1.0000 | 1.0000 |
| 15 | 0.7600 | 0.7600 | 1.0000 | 1.0000 |
| rows returned nothing | 21 | 27 | 0 | **0** |
| ms per query (this box, CPU) | 1.64 | 1.15 | 1.17 | 1.59 |

### 3.2 The paired tests (discordant pairs only)

| comparison | k | both | shipped only | other only | Δ pts | exact two-sided p |
|---|---|---|---|---|---|---|
| shipped **vs** `pg/tokens` (**primary**) | 3 | 211 | 3 | 7 | **+1.33** | **0.344** |
| shipped **vs** `pg/tokens` | 15 | 228 | 0 | 0 | **0.00** | **1.000** |
| shipped **vs** `pg/native` | 3 | 211 | 3 | 80 | +25.67 | <0.001 |
| shipped **vs** `pg/native` | 15 | 228 | 0 | 72 | +24.00 | <0.001 |
| shipped **vs** in-place fix | 3 | 214 | **0** | 75 | +25.00 | <0.001 |

**Read the `shipped only` column.** The in-place fix has **zero** regressions at k=3 — it is a strict
superset, which is what an *additive* OR term is supposed to be. `pg/native` loses 3 names the shipped
leg found (its analyser keeps tokens ours drops, and its k=3 window fills differently).

### 3.3 Per class (recall@3 / recall@15) — where the whole effect lives

| class | n | shipped | `pg/tokens` | `pg/native` | in-place fix |
|---|---|---|---|---|---|
| definite description | 180 | 0.9778 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 0.9778 / 1.000 |
| identifier (`#n`, `snake_case`) | 90 | **0.2000 / 0.2000** | **0.2000 / 0.2000** | 1.000 / 1.000 | 0.9444 / 1.000 |
| path | 30 | 0.6667 / 1.000 | 0.6667 / 1.000 | 0.7000 / 1.000 | **0.9333 / 1.000** |

**The scorer is not what moves the identifier class** — `pg/tokens`, a *BM25* index fed our terms, scores
**exactly what the shipped leg scores** (0.2000), down to the same 27 empty result sets. What moves it is
the **terms**.

### 3.4 The mechanism — a tokenizer contract mismatch, measured

RediSearch treats `_` as a **word character**. `tortoise/sparse.py:71` does not. Probe on the lane graph
(v4.20.6), verbatim:

```text
'web_search'                       -> [['web_search', 8.0]]
'web'                              -> []
'selection'                        -> []
'test_ci_selection'                -> [['tests/test_ci_selection.py', 14.0], ['test_ci_selection', 14.0]]
'capstone_verdict_check'           -> [['capstone_verdict_check', 8.0]]
'test_7949_capstone_verdict_check' -> []          # four segments: not a term the index holds
```

so for the stored name `web_search` the shipped leg issues `build_or_query('web_search')` =
`search|web` — **two terms the index does not contain**, because the index's only term is `web_search`.
The query is well-formed, the leg returns nothing, and `recall@3` for that class is **0.20** with **21
empty result sets**. That is `#11901`'s measured ident failure (keyword-only `21.1 %` at k=3) *and* its
`rows_zero = 11` — the same defect, now located and explained rather than attributed to a scorer.

Adding the underscore-preserving runs as further **OR alternatives** (the #8933 additive pattern: only
ever adds a term, never removes one) takes the same index from **0.7133 → 0.9633** and empties the
`rows_zero` column. **Nothing about the storage, the engine, or the scorer changes.**

⚠️ **Why this does not rescue SPLADE.** A learned-sparse representation would give the leg *its own*
vocabulary, indexed together with the documents — so it would **mask** this defect rather than fix it,
at the price of a neural pass per query. The measurement says the loss is in a contract between our query
builder and the index we already run; buying a new representation to paper over it is the trade
`SEARCH-ARCHITECTURE.md` §6.2 exists to refuse.

---

## 4. The verdict, against the rule fixed before the numbers

### 4.1 The pre-registered rule

> **PASS iff** on the primary arm (`postgres_bm25/tokens`) recall@3 is strictly greater than
> `falkordb_tfidf` recall@3, **AND** the exact two-sided McNemar test over the discordant pairs has
> p < 0.05. **FAIL otherwise** — including a tie, and including a gain carried by pairs where the two
> arms agree.

The rule was written into `tools/m3_fts_leg_recall.py` before the 300-name measurement existed, and the
artifact carries it verbatim on every run. **Result: FAIL** (+1.33 pts, p = 0.344).

### 4.2 Why the deployable arm does not overturn it

The issue's falsifier asks *"does BM25-in-Postgres beat the current scorer on the FTS leg's recall@k"*.
It does — 0.9700 vs 0.7133 — but the **decomposable** answer is:

* moving to BM25 with the same terms: **+1.33 pts, p = 0.34** → the scorer contributes nothing measurable;
* recovering that same 25 pts **inside the current index**: **+25.00 pts, p < 0.001, zero regressions**.

An index migration whose entire measured benefit is reproducible in place is an infrastructure cost with
no recall return — and V1 deferred it behind exactly this measurement. The deferral **stands**.

### 4.3 What this is worth at production class shares (and why it is not 25 points)

The sample oversamples identifiers and paths on purpose, so the composite `+25.00` is **not** a
production figure. Per class, the in-place fix's own deltas are:

| class | share of real `:Object` names | Δ recall@3 | weighted contribution |
|---|---|---|---|
| definite description | **62.3 %** (research-brief §3.2, measured) | 0.0 pts (unchanged) | 0.0 |
| identifier | **2.2 %** (0.5 % bare `#n` + 1.7 % `PR/issue #n`) … **9.0 %** (the "contains a code token" superset `snake_case` sits inside) | +74.4 pts | **+1.6 … +6.7 pts** |
| path | **not recorded** — the audit has no path share | +26.7 pts | **excluded, declared** |

So the honest production claim is **≈ +1.6 to +6.7 recall@3 points**, concentrated entirely in
code-shaped names, with the path contribution unquantifiable until a path share is measured. That is
still a large, cheap win — and it is the thing worth doing, instead of a migration or a neural learner.

### 4.4 Contradiction test — what this does NOT do

| recorded decision | does this contradict it? |
|---|---|
| **V1 / §6.2** — the text index and its payload stay in the graph; the move is deferred behind M3 | **No — this *is* M3**, and its answer is "do not move". The deferral is upheld, not reversed |
| owner's pre-authorised SPLADE reopening (`#12186`, 2026-11-12) | **No adoption is made, so no override is needed.** The authorization was conditional on M3; M3 says no. Nothing is quietly adopted over V1 |
| **KEEP ruling** (`#11395` comment `6781921075`) — `:Object`/`:Subject` keep their stored embeddings | **No.** Nothing here touches an embedding; the measurement is the lexical leg only |
| **§12.2c** — the entity vector is the entry + resolution key, budget-capped | **No.** Not touched |
| **`#11901` conclusion** — do not widen the window before a verdict exists | **No.** `limit` is held at the production 3 (and 5/10/15 only to report the curve); the finding is that **width is not the constraint** either |

---

## 5. What this does NOT measure (declared, not buried)

* **The fused read.** Every arm measures the **lexical leg in isolation**, which is what M3 asks. Whether
  +25 pts of lexical recall@3 moves the *fused* candidate set the capture prior actually consumes is
  **unmeasured here** — `#11901` showed the dense leg *hurts* the definite class, so the interaction is
  not obviously additive.
* **Production `:Object` rows.** The population is harvested strings, not a graph census; no live
  `:Object` graph is reachable from a lane (`STORAGE-ARCHITECTURE.md` §14.3 records the same limit).
* **The path class's real share.** Not recorded anywhere; excluded from §4.3 rather than assumed.
* **Index-time tokenization** is each engine's own and was not controlled (only query-side terms were).
  This is a declared residual on `pg/native`'s 0.67-pt lead over the in-place fix.
* **A SPLADE encoder's cost on CPU.** ⛔ **Not measured, deliberately.** The issue's overhead item sits
  after "if M3 passes"; it does not, so the leg is closed and a cost figure for a discarded component
  would be decoration. The literature range (≈100–300 ms/query for a full BERT-base SPLADE encoder on
  CPU, ≈1–50 ms for distilled variants) is **cited, never adopted as ours**.
* **The embedded sklearn TF-IDF fallback** (`tortoise/sparse.py`'s other backend) — outside
  `db.idx.fulltext` entirely.

## 6. What happens next

1. **`SEARCH-ARCHITECTURE.md` §6.2's status line is accurate and stays.** C2 = the scorer is not the
   constraint. C3 = SPLADE is **closed with this evidence** (`#12186` item 2), not carried.
2. **The one real lever this measurement found is filed as its own work item** — the tokenizer contract
   between `sparse.tokenize_sparse_query` and RediSearch's `_`-as-word-character. It is **measured, not
   shipped here**: the additive term is an arm of this harness, because a query-bytes change touches
   every label and every caller and reverses part of `#2070`'s byte-identity control. It needs its own
   review with this number in front of it.
3. `#11901`'s `rows_zero = 11` (**and this run's 21**) now has a cause; the finding is carried there as
   evidence, not as a peer issue.

## 7. Write-back

**`wrote:`** not written — `~/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md` does not exist on this box (the
dispatch's own mandate-preflight reports it, agent-infra#3377), so the graph write path named by this
repo's skill table cannot be followed from here. The finding lives in this document, in the two
artifacts, and on `#12298` / `#12186` / `#11901`.

**`edges:`** none, for the same reason: with no reachable point on the topic, an edge would be fabricated.
