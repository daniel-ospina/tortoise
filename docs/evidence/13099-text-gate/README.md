---
title: "The text-index quality gate, re-measured — is any Postgres option within nDCG noise of the in-graph leg? (#13099)"
type: evidence
domain: platform
doc_status: draft
created: 2026-11-17
updated: 2026-11-17
issue: "#13099"
subjects.team: epistemic-team
aboutSubjects: text ranking, FalkorDB fulltext index, Postgres full-text search, ParadeDB pg_search, BM25
aboutObjects: SEARCH-ARCHITECTURE.md §6.2 / M3, #13082 phase 2, #4051, #1144 retrieval eval, #12298 M3 recall, #12350 tokenizer contract
---

# The text-index quality gate, re-measured (#13099)

**Issue:** [#13099](https://github.com/daniel-ospina/tortoise/issues/13099) · **PR:**
[#13107](https://github.com/daniel-ospina/tortoise/pull/13107) · **Decision this feeds:**
`SEARCH-ARCHITECTURE.md` §6.2 (*the text index does NOT move yet*) and the Cypher→Postgres plan
`#13082` phase 2. · **Harness:** `tools/text_gate_13099.py` · **Artifacts, this directory:**
`artifact.json` (every arm, every query class, the paired CIs, the depth audit and the worked
example) and `artifact-repeat.json` (a **second independent run**; its metrics, its per-query
metrics and its verdict are **identical** to the first — a determinism check, not a second opinion).
The artifacts carry per-query **metrics** so every paired CI in this record is recomputable by a
reader; the per-query retrieved-id lists are omitted at ~4 MB because the harness regenerates them
exactly (`--full` keeps them). · **Measured:** 2026-11-17, one box, both backends
in one process, twice.

---

## 0. Verdict, with the number

> **Nothing Postgres is behind the in-graph leg any more.** On the query class that carries the
> committed qrels (n=100), Postgres `ts_rank`/`ts_rank_cd` land **+2.32 nDCG@10 pts
> (90 % CI −0.93 … +5.46)** — inside the noise band — and are **significantly ahead at depth**
> (**+9.23 nDCG@100 [+6.68, +11.85]**, **+13.58 recall@100 [+10.46, +16.78]**). `pg_search` BM25 is
> the same shape (+1.87 [−1.90, +5.98] at @10; +15.15 recall@100 at depth). **On no query class does
> any Postgres option lose significantly.**

**So the gate §6.2 rests on does not reproduce on our corpus.** That is the measurement; it is not a
migration and it is not a reversal of the recorded decision — see §6 for exactly what it does and does
not license.

**What it costs to adopt the winner.** `ts_rank` is **Postgres built-in**: no extension, no
background worker, no restart-level config, and a **64 KB** GIN index on this corpus — against
**3.1 MB** for ParadeDB's BM25 index, **48×** smaller. ⚠️ Compare like with like, and read what the
artifact actually measured: the index figures are INDEX-ONLY, while the store figures are not
interchangeable either. `sizes_bytes.tg_docs` is `pg_total_relation_size` — **heap + every index on
the table** — so the table's own bytes are **4.23 MB** (7.58 MB total − 3.13 MB BM25 index − 0.14 MB
GIN − 0.08 MB pkey). Store-to-store, `ts_rank` is heap + its GIN ≈ **4.3 MB** against the FalkorDB
graph's **7.73 MiB** of `used_memory` for the same content + embeddings — **≈ 8.11 MB** in the decimal
units the Postgres figures use (Redis prints `used_memory_human` in 1024-based units, so `7.73M` is
7.73 × 1 024² bytes), i.e. **~47 % smaller**, because the
BM25 index is what made the Postgres row look as large as the graph. `pg_search` carries that index,
so IT is the one that lands at ≈ **7.4 MB**, i.e. comparable to the graph. The saving for `ts_rank`
is real but ordinary in size; the decisive part is the dependency (no extension, no preload, no
worker, no AGPL).

---

## 1. What was asked, and what could not be answered

`SEARCH-ARCHITECTURE.md` §6.2 keeps the text index in the graph on a recorded figure — *"`ts_rank`/
`ts_rank_cd` measured 8–23 nDCG@10 behind BM25"* — and the document's own PROVENANCE paragraph marks
that figure **third-party**. §6.2 then states the honest gap in it: *"how far our hosted
`db.idx.fulltext` leg sits from that baseline is **unmeasured**"*. **This record measures that
distance against the leg we actually run**, which is the only version of the number a decision can
rest on.

⛔ **What this does NOT refute.** The 8–23-point external figure compares Postgres FTS to a BM25
implementation on three foreign datasets. This measurement compares **our leg** to **Postgres on our
corpus**. A result here says nothing about whether that external figure was right; it says that
**the reason §6.2 gives for keeping our index does not hold when the comparison is made against our
index**. Those are different claims and are kept different on purpose.

---

## 2. Preflight — what was and was not available

| surface | state |
|---|---|
| **Search (rung 1)** | `mcp__seo-intelligence__perplexity_search` — **server not registered on this box** (`mcp_load` → *Unknown MCP server*). Same failure `#12298` recorded. |
| **Search (rung 2)** | `web_search` refused — `PERPLEXITY_API_KEY not set`. `~/keys.env` exists (0600) and carries **no** Perplexity key. **External research was therefore unavailable**, so every operational fact below is **measured from the deployed artifact itself** (extension control files, package metadata, `pg_settings`, relation sizes) rather than quoted from a vendor page. Where a fact is inherited from a prior record, it says so. |
| **Graph (internal)** | `#12298`'s record and `SEARCH-ARCHITECTURE.md` read in full. The graph brief `~/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md` does not exist on this box. |
| **`AGENT_INFRA_PATH`** | **unset in this session** — so `scripts/parallel_work_check.sh` (a `commit-workflow` gate) was **not run**. ⛔ Not "could not run": the checker is reachable at the vendored path (`/home/cmux/agent-infra/scripts/parallel_work_check.sh`, and it reads no `AGENT_INFRA_PATH` at all), so this is a record of what the run did, not a claim about what was possible. Recorded, not worked around. |

**Backends actually deployed** (both in Docker, both measured):

- **FalkorDB `v4.20.6`** via `tools/test_lane.py` — the authoritative lane `#1144`'s own baseline
  note names (see §3).
- **PostgreSQL 16.15** (`paradedb/paradedb:0.26.6-pg16`) — the same Postgres major the record
  substrate targets, and the same `pg_search` version `#12298` used, so the BM25 arm is comparable to
  that record.

---

## 3. The corpus — reused, not rebuilt, and one anchor correction

| element | what was used |
|---|---|
| corpus | `benchmarks/synthetic_corpus.generate_oracle_points`, **seed 42, 2000 points, 24 latent topics** |
| queries | `tests/eval/retrieval/queries/oracle_queries.json` — the **100 committed oracle queries, byte-identical** |
| judgements | `benchmarks.synthetic_corpus.oracle_grades_for_query` — grade 2 = target topic, 1 = NEAR (bridge-sharing), 0 = rest |

**It is the same corpus the earlier nDCG work used (#1144), and that is the whole point.** The
grading is derived from the seed, so no judge, no human label and no new annotation enter this
measurement. A fresh corpus would have made the figures incomparable to the recorded ones — the most
likely way to get a wrong answer here.

### ⚠️ The stored anchor is retracted by its own notes, so the baseline is re-measured

The committed baseline (`tests/eval/retrieval/baseline/baseline-embedded-model-2026-11-08.json`)
records `fts nDCG@10 = 0.8143 [0.7565, 0.8651]` — and its own `notes` field **withdraws that column
as evidence**:

> *"FTS/structural columns on embedded runs are measurement-environment artifacts, not quality
> statements — Docker FalkorDB >= 4.x is authoritative."* … *"FTS populated 40/100 queries."*

Quoting `0.8143` as "the baseline" would therefore have been quoting a number its own record
disowns. **The baseline here is measured on a real Docker FalkorDB lane** — the environment that note
names authoritative. It came out at **0.8171**, close to the embedded figure, which is reassuring but
is a *result*, not an assumption: had the two disagreed, the Docker number is the one that counts.

---

## 4. The three query classes, and the one that cannot be read as evidence

The committed grade function depends only on the **target topic**, never on the query text. That is
what lets all three classes reuse the *committed* judgements unchanged.

| class | n | how it is built |
|---|---|---|
| **short keyword** | 100 | the committed oracle queries, byte-identical (the anchor class) |
| **natural-language question** | 100 | the same content tokens wrapped in question scaffolding, template chosen by a seeded RNG |
| **entity name** | 96 | a stored document's own text used verbatim — four per topic, chosen by sorted id |

⚠️ **The question class is DEGENERATE here and is reported as non-evidence.** It was measured, not
merely asserted: **96.46 %** of the 650 scaffold tokens it adds are absent from the corpus token pool
(`question_class_evidence` in the artifact), and **no candidate prices an unmatched query term** — no
coordination-level or penalty term exists in `db.idx.fulltext`, in `ts_rank`/`ts_rank_cd`, or in
`pg_search` BM25. The predicted consequence is visible in the data: the four **lexical** arms
reproduce the keyword class **to six decimals**, and the two hybrid arms move only by the amount the
question's own wording shifts the (constant-across-hybrids) vector leg — §5.3 prints both. It is
published for completeness and **must not be counted as independent corroboration** in either
direction. Anyone reading this record should treat the evidence base as **two** classes, not three.

⚠️ **The entity-name class is a proxy, and the record says so.** The corpus has no real entity names;
its nearest stored "name" is a document's own text. What it tests is the exact-lookup surface — and
that is where the in-graph leg trails most sharply **at the head** (§5.2, mechanism in §5.4). It is
**not the only** place it trails: the anchor class trails at depth too (§5.1, and §5.4 says so), which
is why no sentence here says "the one deficit".

---

## 5. The four-way table

**All arms, per class.** Metrics are means over the class. Δ columns are **paired 90 % bootstrap CIs
on (arm − `in_graph_fulltext`)**, in points, 2000 resamples — the instrument `#1144`'s pre-registered
gate already uses. `returned` is the mean number of rows the arm actually produced per query (out of
`LIMIT` = 100) — published because an arm that cannot return 100 rows has a lower `nDCG@100` ceiling
for a reason that is **not ranking**.

### 5.1 `short_keyword` (n=100) — the class carrying the committed qrels

| arm | nDCG@10 | nDCG@100 | recall@100 | Δ nDCG@10 | Δ nDCG@100 | Δ recall@100 | returned |
|---|---|---|---|---|---|---|---|
| `in_graph_fulltext` (baseline) | 0.8171 | 0.7241 | 0.6785 | — | — | — | 92.9 |
| `pg_ts_rank` | 0.8403 | 0.8164 | 0.8143 | **+2.32** [−0.93, +5.46] | **+9.23** [+6.68, +11.85] | **+13.58** [+10.46, +16.78] | 92.9 |
| `pg_ts_rank_cd` | 0.8403 | 0.8164 | 0.8143 | **+2.32** [−0.93, +5.46] | **+9.23** [+6.68, +11.85] | **+13.58** [+10.46, +16.78] | 92.9 |
| `pg_ts_rank_simple` | 0.8517 | 0.8287 | 0.8250 | +3.45 [+0.10, +7.01] | +10.47 [+7.92, +13.12] | +14.65 [+11.58, +17.82] | 92.7 |
| `pg_bm25` (`pg_search`) | 0.8358 | 0.8252 | 0.8300 | **+1.87** [−1.90, +5.98] | +10.12 [+7.10, +13.16] | +15.15 [+11.40, +18.88] | 92.7 |
| `hybrid_baseline` — RRF(in-graph, pgvector) | 0.8531 | 0.8176 | 0.8052 | +3.60 [−0.18, +7.47] | +9.36 [+6.94, +11.99] | +12.67 [+9.64, +15.88] | 100.0 |
| `hybrid_bm25` — RRF(pg BM25, pgvector) | 0.8470 | 0.8418 | 0.8384 | +2.99 [−1.09, +7.47] | +11.78 [+8.37, +15.38] | +15.99 [+11.76, +20.43] | 100.0 |
| ⛔ `in_graph_fulltext_raw` (diagnostic, **not a candidate**) | 0.3908 | 0.2634 | 0.2414 | −42.64 [−50.09, −35.20] | −46.06 [−51.10, −40.82] | −43.72 [−48.34, −39.05] | 20.3 |

### 5.2 `entity_name` (n=96) — the exact-lookup surface

| arm | nDCG@10 | nDCG@100 | recall@100 | Δ nDCG@10 | Δ nDCG@100 | Δ recall@100 | returned |
|---|---|---|---|---|---|---|---|
| `in_graph_fulltext` (baseline) | 0.9513 | 0.8367 | 0.7064 | — | — | — | 99.8 |
| `pg_ts_rank` | **1.0000** | 0.9873 | 0.9977 | **+4.87** [+2.44, +7.44] | **+15.06** [+12.90, +17.26] | **+29.13** [+25.61, +32.66] | 99.8 |
| `pg_ts_rank_cd` | 1.0000 | 0.9873 | 0.9977 | +4.87 [+2.44, +7.44] | +15.06 [+12.90, +17.26] | +29.13 [+25.61, +32.66] | 99.8 |
| `pg_ts_rank_simple` | 1.0000 | 0.9934 | 0.9983 | +4.87 [+2.44, +7.44] | +15.66 [+13.52, +17.82] | +29.20 [+25.67, +32.74] | 99.8 |
| `pg_bm25` (`pg_search`) | 1.0000 | **0.9947** | **1.0000** | +4.87 [+2.44, +7.44] | +15.80 [+13.62, +17.96] | +29.36 [+25.83, +32.92] | 99.8 |
| `hybrid_baseline` | 0.9968 | 0.9623 | 0.9409 | +4.55 [+2.32, +6.93] | +12.55 [+11.21, +13.93] | +23.45 [+21.18, +25.73] | 100.0 |
| `hybrid_bm25` | 1.0000 | 0.9939 | 1.0000 | +4.87 [+2.44, +7.44] | +15.71 [+13.57, +17.84] | +29.36 [+25.83, +32.92] | 100.0 |
| ⛔ `in_graph_fulltext_raw` (diagnostic, **not a candidate**) | 0.8607 | 0.4701 | 0.3869 | −9.06 [−13.54, −4.82] | −36.67 [−40.22, −33.26] | −31.95 [−36.23, −27.58] | 34.2 |

### 5.3 `natural_language_question` (n=100) — reported, but **not evidence** (see §4)

Included so the three-class request is answered in full. Read §4 before using any number in this
table. The four **lexical** rows are identical to §5.1 to six decimals — that is the degeneracy §4
measures. The `raw` diagnostic collapses to 0.0 (see below), and the two **hybrid** rows differ from
§5.1 by ~0.3–0.4 pts at @10 because their vector leg is fed the question text, so the wording does
reach them. No row here carries independent lexical signal.

| arm | nDCG@10 | nDCG@100 | recall@100 | Δ nDCG@10 | Δ nDCG@100 | Δ recall@100 | returned |
|---|---|---|---|---|---|---|---|
| `in_graph_fulltext` (baseline) | 0.8171 | 0.7241 | 0.6785 | — | — | — | 92.9 |
| `pg_ts_rank` | 0.8403 | 0.8164 | 0.8143 | +2.32 [−0.93, +5.46] | +9.23 [+6.68, +11.85] | +13.58 [+10.46, +16.78] | 92.9 |
| `pg_ts_rank_cd` | 0.8403 | 0.8164 | 0.8143 | +2.32 [−0.93, +5.46] | +9.23 [+6.68, +11.85] | +13.58 [+10.46, +16.78] | 92.9 |
| `pg_ts_rank_simple` | 0.8517 | 0.8287 | 0.8250 | +3.45 [+0.10, +7.01] | +10.47 [+7.92, +13.12] | +14.65 [+11.58, +17.82] | 92.7 |
| `pg_bm25` (`pg_search`) | 0.8358 | 0.8252 | 0.8300 | +1.87 [−1.90, +5.98] | +10.12 [+7.10, +13.16] | +15.15 [+11.40, +18.88] | 92.7 |
| `hybrid_baseline` | 0.8504 | 0.8210 | 0.8086 | +3.32 [−0.56, +7.19] | +9.69 [+7.16, +12.33] | +13.01 [+9.87, +16.21] | 100.0 |
| `hybrid_bm25` | 0.8436 | 0.8451 | 0.8429 | +2.64 [−1.43, +7.06] | +12.11 [+8.63, +15.74] | +16.44 [+12.03, +20.81] | 100.0 |
| ⛔ `in_graph_fulltext_raw` (diagnostic) | 0.0000 | 0.0000 | 0.0000 | −81.71 [−86.55, −76.43] | −72.41 [−76.39, −68.27] | −67.85 [−72.43, −63.09] | 0.0 |

**The `raw` row is the clearest single demonstration of *why* the class is degenerate, and of a
second fact worth recording:** with the raw question text, RediSearch's space-separated groups are an
implicit **AND**, so it returns **zero** rows for all 100 queries (mean `returned` 0.0). The shipped
path never sends a raw question — it sends `build_or_query`'s OR-union — which is exactly why the
builder is part of the measurement and not a detail.

### 5.4 Why the in-graph leg loses on exact names — a worked example, from the artifact

`worked_examples[0]` in `artifact.json`, reproducible from the harness:

```text
query   "cap-dominated boot cold-start band verdict"        (a live stored document, p-0000022)
in-graph top-1   p-0000023  score 9.0000  grade 1   ("cap-dominated fingerprint provenance handoff sha git")
in-graph target  p-0000022  score 4.1429  rank 99   (matches ALL SEVEN query terms)
pg_ts_rank       p-0000022  rank 1
```

A document matching **two** of the query's seven terms outranks — by 2.17× — the document that
matches **all seven**, because the two rare terms (`cap`, `dominated`, shared with a NEAR topic)
carry more weight than five common ones. That is the shipped scorer's behaviour, not a harness
artifact: both documents are `status=live` and pass the same product filter, and the compound is
split identically on both sides (`cap-dominated` alone matches **0** documents; `dominated` matches
the target first).

**This is the #8488 / #12350 defect class wearing a different hat**, and it matters for the verdict:
the place the in-graph leg genuinely trails **at the head** is a **ranking-property deficit fixable in
place**, not a reason the store is wrong — and it trails at depth on the keyword class too (Δ nDCG@100
+9.23, §5.1), so this is not the only place it trails. `#12298` reached the same structural conclusion
for `_`-carrying identifiers: a 25-point gap that was the query builder, not the scorer.

### 5.5 What the numbers say, read honestly

- **The 8–23 point deficit does not appear.** At `nDCG@10` the gap is **+2.32 points in Postgres's
  favour and not significant**; the recorded direction (Postgres behind) is **not observed at all**.
- **The real separation is at depth**, not at the head: `nDCG@100` and `recall@100` favour Postgres by
  9–29 points with CIs that exclude 0. `ts_rank` has no IDF and no length normalisation — the
  documented weakness — and on **this** corpus that weakness never costs it the head, because (a) the
  vocabulary is small so rarity carries little information, and (b) coverage of the query rewards it.
- **`ts_rank` and `ts_rank_cd` are identical to six decimals here.** Recorded rather than hidden: with
  the default normalisation the cover-density variant does not reorder anything on this corpus, so it
  is **one candidate, not two**.
- **The dictionary matters more than the scorer**: `simple` beats `english` by ~1.1 points at the head
  (+3.45 vs +2.32). Established corpora are already stemmed, so a stemmer only adds collisions.
  Recorded as sensitivity, **not** used to pick a winner post-hoc.
- **At fusion the lexical swap is nearly invisible** — `hybrid_bm25` vs `hybrid_baseline` at @10 is
  0.8470 vs 0.8531 (both CIs span 0), and BM25's hybrid is ahead only at depth. That is expected:
  `rrf_fusion` is **rank**-based and discards each leg's score.

---

## 6. Operational facts — the part that actually decides adoption

Measured from the deployed artifacts (no vendor page was consulted; see §2):

| candidate | extension / dependency | index build (2 000 docs) | index size | background worker / config | licence |
|---|---|---|---|---|---|
| **in-graph `db.idx.fulltext`** (baseline) | none — FalkorDB built-in | 0.001 s (created on an already-seeded graph; seeding alone is 1.174 s, 1.226 s on the repeat run) | not separately exposed; the whole graph incl. index is **7.73 MB** `used_memory` (7.74 MB on the repeat run) — content **and** `vector(384)` embeddings | none, but the text lives in **RAM at the graph's cost** — the ~$73/GB-month (⛔ **inherited**, `docs/architecture/STORAGE-ARCHITECTURE.md` §16.1 ≈ `$0.10/GB-hour`; it is in NEITHER artifact) that motivates the migration | FalkorDB |
| **`ts_rank` / `ts_rank_cd`** | **built-in** (`tsvector`, GIN) | **0.011 s** (repeat 0.012 s) | **64 KB** GIN (+ 72 KB for the `simple` variant) | **none** — no extension, no preload, no worker | PostgreSQL |
| **`pg_search` BM25** | `pg_search` **0.26.6**, `requires = 'vector'`, **not relocatable** | **0.026 s** (repeat 0.025 s) | **3.13 MB** — **48× the GIN index** | **`shared_preload_libraries` change required** (`pg_search,pg_cron,pg_stat_statements` in this image) plus a `paradedb.min_rows_per_worker = 300000` parallel-build threshold | **AGPL-3.0-or-later** |
| **hybrid (RRF)** | needs both legs in one store | — | — | none of its own — RRF is application-side | — |

⚠️ **Index size is not store size, and `tg_docs` is not the table.** The GIN/BM25 cells are
INDEX-ONLY, so index-to-index the GIN index is 48× smaller (64 KB vs 3.13 MB). The Postgres store
figures are `pg_total_relation_size`, which is **heap + every index on the table** — so they are not
comparable to each other until the indexes are separated: the heap is **4.23 MB** (7.58 MB total −
3.13 MB BM25 − 0.14 MB GIN − 0.08 MB pkey). Store-to-store, `ts_rank` is heap + GIN ≈ **4.3 MB**
against the FalkorDB graph's **7.73 MiB** `used_memory` for the same content + embeddings (≈ **8.11 MB**
in decimal units — Redis prints `used_memory_human` 1024-based — hence ~47 %
smaller), while `pg_search` — which carries the 3.13 MB index — lands at ≈ **7.4 MB**, comparable to
the graph. The win for `ts_rank` is the dependency (no extension, no preload, no worker, no AGPL) and
the index, not an order of magnitude of storage.

**Two deployment caveats, stated rather than buried:**

1. **`pg_search` is not on Supabase's hosted platform** — it is self-managed only. This is inherited
   from `#12298`'s record and **was not re-verified here** (no research key on this box, §2). It
   constrains *where* the BM25 arm could ever run, and it is a serious constraint for a managed
   deployment.
2. **AGPL-3.0-or-later** is read from the image's own package metadata
   (`/usr/share/doc/postgresql-16-pg-search/copyright`). For a product that may ship or embed its
   graph engine, that is a licensing question, not a footnote — and it is the strongest single reason
   **`ts_rank` is the candidate to prefer over `pg_search`**: `ts_rank` buys the same head quality and
   is ahead of `pg_search`'s BM25 hybrid on nothing the decision turns on, at 1/48 the index size,
   with no preload and no AGPL.

---

## 7. What this measurement licenses — and what it does not

**It licenses:** the statement that **the quality gate §6.2 states as the reason to keep the text
index does not reproduce** on our corpus and our leg. Every Postgres option measured is within noise
at the head and significantly ahead at depth; none loses.

**It does NOT license any of these, and this lane does none of them:**

- ❌ **Reversing §6.2.** The recorded decision is unchanged by this record. A measurement that
  contradicts a decision's *stated reason* is the **evidence for a reopen**, argued on the decision's
  own home (`#4051` / `#13082` phase 2) — never a quiet adoption, and never a lane's own call.
- ❌ **Migrating the text index.** No product code is touched by this PR.
- ❌ **Refuting the external 8–23 figure** (§1).
- ❌ **Claiming a production-quality prediction.** See the limits below.

### Limits, stated so the number is not over-read

1. **The corpus is synthetic.** A 131-token declared pool (`benchmarks.synthetic_corpus.TOKENS`) over
   24 topics, ~5.5 core tokens each, is a
   *relative* instrument — it can order candidates and it cannot predict absolute production nDCG.
   It is the repo's committed deterministic corpus, chosen **because** comparability to the recorded
   figures requires it, not because it is representative.
2. **One corpus, one seed, one box.** The repeat run reproduces it exactly, which establishes
   determinism and not generality.
3. **The three classes are not equally strong evidence.** The keyword class carries committed qrels
   and n=100. The question class is measurably degenerate (§4). The entity-name class is a proxy
   (§4) and its finding is explained mechanistically (§5.4), so it should be read as a *mechanism*,
   not as a headline quantity.
4. **No production query log was available**, so there is no weighting by real query traffic. `#4051`
   records that we have no dataset (§1 of `SEARCH-ARCHITECTURE.md`), and that has not changed.

---

## 8. Reproduce it

```bash
# 1. the graph lane (never the shared localhost:6379 URI on a cell)
uv run python tools/test_lane.py uri          # note the printed port

# 2. the Postgres lane
docker run -d --name pg13099 -e POSTGRES_PASSWORD=s3cret -e POSTGRES_DB=tg13099 \
    -p 15499:5432 paradedb/paradedb:0.26.6-pg16
docker exec pg13099 psql -U postgres -d tg13099 -c 'CREATE EXTENSION vector; CREATE EXTENSION pg_search;'

# 3. measure (seeds both backends from the committed corpus, runs all arms)
uv run --with "psycopg[binary]" python tools/text_gate_13099.py run \
    --fg-port <lane port> \
    --pg-dsn 'postgresql://postgres:s3cret@127.0.0.1:15499/tg13099' \
    --out docs/evidence/13099-text-gate/artifact.json

# 4. repeat for the determinism check
uv run --with "psycopg[binary]" python tools/text_gate_13099.py run ... --out .../artifact-repeat.json
#    add --full to also write the per-query retrieved-id lists (~4 MB)
```

`tests/test_text_gate_13099.py` pins the parts that must not drift: the corpus is byte-deterministic,
the committed queries are reused verbatim — **including a SHA-256 of the committed query file**
(`f39c035c291fb5c369e86b43048190a16b55423f1cdaa9a86983c9e8456dd1c1`), because the tables below are only
comparable to the query set that produced them and a path string does not establish that — and, the
one that matters most, **the decision rule keys on the CI's direction**, so a significant *gain* can
never be read as a disqualification; an arm that raised is **disqualified rather than scored**
(inherited by the hybrids built from it), and a run whose **baseline** raised publishes no verdict at
all.

The same file recomputes §5's numbers from `artifact.json` — every arm's three metrics, all three Δ
cells of every non-baseline row (21 per class, not "six"), and the `returned` column — and requires
§5's row set to equal the artifact's (a deleted arm row is drift). §6 is pinned **by row**: its build,
size and memory figures, including the repeat run's, must appear in the row of the backend that
produced them — a whole-section presence check would pass a table whose two labels had been swapped,
which is the shape §6's figures were actually wrong in. §0/§6's store-to-store figures are pinned with
the heap derivation they rest on (`tg_docs` is `pg_total_relation_size`, i.e. heap + every index). §6's
prose facts are held to the same rule, **by row**: the licence cell, the `pg_search` version, the
`requires`/relocatability cell and the exact `shared_preload_libraries` list are each required to
appear in the row of the backend that produced them. They are not *derived* from `artifact.json` —
they are read from the image (§6's caveats say where) — so the pin is a presence check, and ⛔
`copy_s` is **not** covered by any pin at all: §6 does not print it, so it lives in the artifact and
is guarded by nothing. Stated rather than implied to be covered.

⚠️ **The two committed artifacts predate record keys this harness now writes**, and a rerun will
differ structurally from them, not only in timings. Five keys need reading with that in mind:

- `corpus.queries_sha256` and `results[*].arm_errors` are **absent** — they only appear on the next
  measurement run, which is why the query digest is also stated in prose above, and why the
  disqualification path is pinned by unit tests rather than by the committed artifacts.
- `corpus.postgres_seed.sizes_bytes.tg_docs_heap` / `tg_docs_indexes` are **absent** for the same
  reason, so §6's heap figure is DERIVED here from the recorded per-relation totals
  (`tg_docs` − every index) rather than read; the harness records the split directly from the next
  run on, and the pin derives it the same way so the committed record stays auditable.
- `tokenizer_fallbacks` **is** present, but it was appended when the `verdict` blocks were regenerated
  (it sits AFTER `verdict` in the file; a fresh `run()` writes it BEFORE), so its `[]` is not a
  reading taken by the original run — it is a separate measurement over the committed query set
  (all 296 query texts, 0 raising and 0 returning an empty token list), which is why the value is
  trustworthy while its position is not the one a rerun produces.
- **The FalkorDB server version (`v4.20.6`, §2) is recorded in NO artifact** — `graph_seed` carries
  `fulltext_index_build_s`, `graph_memory_usage`, `points` and `seed_s` only, while the Postgres seed
  records its own `server_version`. The comparability argument in §3 rests on WHICH graph build ran
  the measurement, so this is the one deployment fact in the record that no committed file can
  corroborate; it is asserted from the lane, not pinned. `seed_graph` already shells `redis-cli INFO`
  for memory, so the next run can record `redis_version` beside it and a pin can follow.

The `verdict` blocks themselves ARE regenerated from each file's own `results`, so the decision rule
and the published conclusion cannot diverge.
