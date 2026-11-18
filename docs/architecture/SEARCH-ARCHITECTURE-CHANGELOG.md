---
title: "Search Architecture — Changelog"
type: engineering
domain: platform
doc_status: live
created: 2026-11-11
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: search
aboutObjects: SEARCH-ARCHITECTURE.md, absent belief state, search design revisions
---

# Search Architecture — Changelog

> **What this is.** The record of *how the search design got where it is* — the work `SEARCH-ARCHITECTURE.md`
> drove and the provenance of its rulings. It is the counterpart of `STORAGE-ARCHITECTURE-CHANGELOG.md`, whose
> issue map (moved from `STORAGE-ARCHITECTURE.md` §13) is the sibling shape; `SEARCH-ARCHITECTURE.md` has never
> carried a section→issue index, so there is no map to move yet, and its review record lives on `#4051` (§10
> says where the corrections that changed the decision are stated).
>
> **What `SEARCH-ARCHITECTURE.md` is.** The design. It states where the vector index lives, what the graph
> engine keeps, and the evidence behind each ruling; it does not narrate its own revisions or track the
> state of the issues it drove.
>
> **Why the narration moved here.** The documents the narration guard polices (`DESIGN_DOCS` in
> `tests/test_extractor_v4_citation_rot.py`: `ONTOLOGY.md`, `EXTRACTOR-V4-ARCHITECTURE.md`,
> `STORAGE-ARCHITECTURE.md`, `SEARCH-ARCHITECTURE.md`) each carried the opposite of their own header, and the
> guard that pins the class is
> `tests/test_extractor_v4_citation_rot.py::test_the_design_documents_carry_no_issue_state_or_change_narration`.
> `ONTOLOGY.md`'s move is `#8745`; this document's is `#11907`, with the same convention as the two
> siblings that predate it (`STORAGE-ARCHITECTURE-CHANGELOG.md`,
> `EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md`). Nothing is deleted: the design statements stayed in the design
> document (reworded to state the design rather than the tracker), and the record of the work is here.

---

## `#11702` — absent belief state is `null` with `ep_state: "absent"`, never the neutral 0.5

Landed by `#11803` (`fix(11702): absent belief state is null with ep_state, never 0.5`), which added the
rule to `SEARCH-ARCHITECTURE.md` §6.3 as tracker state (`**✅ FIXED (#11702) — kept here as the reason the
rule exists…**`), and to §1.5 item 5 as `**landed** (#11702, implementing the §6.3 decision of PR #11481)`.

**The rule it states** (kept in the design document, §6.3): a claim with **no persisted belief** used to
answer the neutral Beta(1,1) mean 0.5, and the ranker coalesced the value to it — indistinguishable from a
computed one at the point where ordering happens. The rule that replaced it is **absent state is `null`
with `ep_state: "absent"`, never 0.5**, and it holds on every read/rank surface: `annotate_ep_batch`, the
canonical `why` `ep` block (`tortoise/why.py`), `GraphRanker`, `StateRanker`/`recall_ranking`,
`GapsRanker`/`gaps_ranking`, `SubgraphExpander`, and the event/object aggregate reads; the `analyze`
templates refuse an absent claim rather than admitting it at a coalesced 0.5. The vocabulary is declared
ONCE (`search_engine.EP_STATE_*` + `ep_has_belief_cypher`/`ep_state_cypher`/`ep_state`/`ep_state_resolve`),
and `tests/test_11702_absent_belief_state.py` pins the property across every one of those surfaces, in both
directions (a *measured* 0.5 keeps its 0.5).

**Why it was red on `main` rather than caught at merge time.** `#11803` merged at **15:29:11Z**; its own
`pull_request` run (`43047153399`, head `2fa7d2fe…`) carried a `test (g)` check-run that **started at
17:16:57Z** and red this nodeid at **17:30:08Z** — so the guard *was* in that shard's population, and the
shard had simply not started when the merge landed. Nothing had concluded against it; the merge gate did not
wait. **Why a required shard can start ~1h48m after its merge is unresolved** — `#11907` carries the
follow-up, and its "shard population" reading is that issue's hypothesis, not this record's. `test (g)` is a
required shard, so the violation made every PR on the repository unmergeable.

**What moved out of the design document on 2026-11-11**, and what stayed:

- §1.5 item 5 kept the *requirement* (the rule is machinery that must exist before private-beta) and lost
  the tracker state (`landed`, the issue reference, and "what remains of it is the per-surface sweep").
- §6.3 kept the *design statement* — the rule, why the coalesced 0.5 is unsound, and the full surface list
  above — and lost the `✅ FIXED (#11702)` header and the "used to answer… replaced it" framing.
- Nothing about the rule's content, its surface list, or `tests/test_11702_absent_belief_state.py`'s
  coverage was dropped or loosened; the guard was not widened and `DESIGN_DOCS` was not narrowed.

---

## `#12298` — M3 measured: the text index does not move, and the recall gap was never the scorer

`SEARCH-ARCHITECTURE.md` §6.2 defers the text index behind measure **M3** — *"a BM25-capable Postgres
option with score distributions, not only ranks, plus the identifier fixture"* — and §9's M3 row
states the same. `#12298` ran that measurement on the `#11901` corpus with its instrument
(`tools/m3_fts_leg_recall.py`, artifacts in `docs/research/2026-11-13-12298-m3-fts-leg/`), and its
result answers M3's question: **the text index does not move**.

**The numbers** (300 names, paired — one pinned population, one run, both backends, repeated with an
identical second run):

| arm | recall@3 | Δ vs shipped | paired p |
|---|---|---|---|
| shipped `db.idx.fulltext` (RediSearch TF-IDF) | 0.7133 | — | — |
| ParadeDB `pg_search` BM25, **our production terms** | 0.7267 | +1.33 pts | 0.34 |
| ⤷ the same, tokens-controlled subset (174 of 300 queries) | 0.5287 vs 0.5172 | +1.15 pts | 0.73 |
| ParadeDB `pg_search` BM25, its own analyser | 0.9633 | +25.00 pts | <0.001 |
| **the shipped index**, query with the `_`-preserving runs added | 0.9633 | +25.00 pts | <0.001 |

**What that says, in the decision's own terms.** The scorer is not the constraint: with the query terms
held constant, swapping TF-IDF for BM25 moves recall@3 by 1.33 points (1.15 on the subset where the
control holds), is **−0.67 at k=5** (the shipped leg ahead 2–0) and **zero** at k=15, with zero
discordant pairs at k=15. Both directions are stated because reporting only k=3 and k=15 reads as a
floor where the evidence has a sign change. The 25-point difference is real, is
**not BM25**, and is reproducible **inside the current index** — so an index migration earns no recall
return, which is what §6.2's deferral exists to establish.

⛔ **This entry does NOT close `C3` (SPLADE), and an earlier revision of it wrongly said so.** `#12298`'s
step 3 is a recorded owner instruction to proceed into SPLADE without re-litigation, and the literal
falsifier it states is *not* met — a deployable BM25-in-Postgres does beat the current scorer. The
measurement is evidence that C3's **premise** (a ranking limit in the text index) is falsified, and the
brief records that as a recommendation; the ruling on C3 stays in the OPEN homes — epic `#12186` and the pre-registration issue `#12192` — where the owner can take it. ⛔ NOT on `#12298`: that issue is **CLOSED** (`COMPLETED`, 2026-11-13T05:21:53Z) on the reading *"M3 FAILS"*, which this measurement contradicts: the closing condition in its own falsifier sentence is NOT triggered — a deployable BM25-in-Postgres beats the current scorer.
A lane may no more decline pre-authorised scope on its own reading than it may adopt something over a
decision.

**The `#8933` identifier fixture is part of the DoD, and the shipped leg fails it.** Five fixture records
queried at the production window: shipped **3 of 5 reachable**, the in-place query **5 of 5**, BM25 fed
our terms **3 of 5**, BM25 with its own analyser **5 of 5**. The `abc_def` row is **absent from its own name's window at the production
limit and at k=15** on the shipped leg. The identifier capability the product believes it has is defeated on exactly
the shapes `#8933` was built for.

**The defect it found, stated as a design fact rather than as tracker state.** RediSearch treats `_` as a
word character; `sparse.tokenize_sparse_query` (`tortoise/sparse.py:662`, splitting on `[^a-z0-9]` at
`:67`) does not. So for a stored name `web_search` the leg issues `search|web` — two terms the index does
not contain — and `snake_case` names are unretrievable by their own name: of the 73 in the sample,
**1 was found at k=3 by the shipped leg and 68 by the in-place query**. The repair is additive (the
`#8933` alias pattern — an OR alternative, never a removed term) and it has **zero** regressions across
the sample. It is **measured in this harness and not shipped** — that is the state at THIS measurement;
the reason it was withheld (a query-bytes change touches every label and reverts part of `#2070`'s
byte-identity control) is exactly what `#12368` then answered with the number in front of it, and it is
shipped there (see the entry below).

---

## `#12368` — the `_`-preserving composite shipped, on both surfaces

`#12298`'s M3 finding ended with the one repair it could not land on its own authority: the index holds
`web_search` as **one** term while `build_or_query` emitted `search|web`, so the record was
unretrievable by its own name. `#12360` asked for that repair and `#12368` lands it in the one seam
every text read shares (`tortoise/sparse.py::build_or_query`), as an **additional OR alternative** —
`underscore_run_terms` rides after the originals, the `#8933` alias/phrase tails and the `#2070`
expansion tail, and no term is ever removed.

**What it costs and what it buys, at the seam.** Additive-only by construction, and byte-identical for
every query carrying no `_` at all: measured over this bundle's own 300-name population, the emitted
string changes for **87** of them — exactly the names whose §3.4 mechanism row says the leg emitted terms
the index never holds. Measured on the real read path (not a harness), candidate recall@3 over that
population moves 0.7133 → 0.9633 with **zero** regressions, and the same on both fused surfaces.

**The tokenizer contract is NOT what was repaired.** `tokenize_sparse_query` still splits on `_`, so
`#12350`'s question is untouched, and this entry is not a ruling on it. What the repair rides is the
query side, which is why every query without a `_` is byte-for-byte the old one — the property `#2070`'s
control depends on and the reason the review that withheld this in `#12298` was right to withhold it
until the number existed.

⚠️ M3's published tables were measured before this tail existed and were **not** re-run here — a
re-measurement needs a live FalkorDB lane and a `pg_search` Postgres. Its brief §3.4 records that
disclosure in place; the numbers there are a record of the pre-`#12368` emission, not of what ships.

---

## `#13099` — the nDCG gate re-measured: no Postgres option is behind the in-graph leg

`#12298` closed M3 measuring **recall**, and its own note recorded what that left open: the qualifier
asking *how far our leg sits from the 8–23-point BM25 baseline* is a **ranking-distance (nDCG)**
question, and it stayed unmeasured. `#13099` measures it, on the corpus the earlier nDCG work used so
the figures are comparable.

**What was run.** The `#1144` committed deterministic corpus (seed 42, 2000 points, 24 topics) with its
**100 committed oracle queries, byte-identical**, and the **committed grade function** — so no label is
invented, human or LLM. Both backends live: a Docker **FalkorDB v4.20.6** lane (the environment the
`#1144` baseline's own notes name authoritative — its embedded `fts nDCG@10 0.8143` is retracted there
as an artifact, "FTS populated 40/100 queries", so the baseline was **re-measured at 0.8171** rather
than quoted) and **PostgreSQL 16.15** with **`pg_search` 0.26.6** — the same Postgres major and the same
`pg_search` version `#12298` used, so the BM25 arm is comparable to that record.

**The numbers** (paired 90 % bootstrap CIs, 2000 resamples, against `in_graph_fulltext`; n=100 on the
class carrying the committed qrels):

| arm | nDCG@10 | Δ nDCG@10 | Δ nDCG@100 | Δ recall@100 |
|---|---|---|---|---|
| `in_graph_fulltext` (baseline) | 0.8171 | — | — | — |
| `pg_ts_rank` / `ts_rank_cd` | 0.8403 | **+2.32** [−0.93, +5.46] | **+9.23** [+6.68, +11.85] | **+13.58** [+10.46, +16.78] |
| `pg_search` BM25 | 0.8358 | +1.87 [−1.90, +5.98] | +10.12 [+7.10, +13.16] | +15.15 [+11.40, +18.88] |

**No Postgres option loses significantly on any query class.** At the head, `ts_rank` is **inside the
noise band**; at depth it is **significantly ahead**. On the exact-name surface (n=96) it is ahead by
**+4.87 nDCG@10** [+2.44, +7.44] — and that deficit is explained rather than assumed: a document
matching 2 of a query's 7 terms scores 9.0 while the document matching all 7 scores 4.14 and ranks
99th, which is the `#8488`/`#12350` defect class and is **fixable in place**.

**What changed in this document: nothing about the decision.** The §6.2 decision to keep the text
index **stands**, and the external 8–23 figure is **not refuted** — that compares Postgres to BM25 on
foreign datasets, while this compares *our* leg to Postgres on *ours*. What changed is one factual
claim: the qualifier §6.2 recorded as **unmeasured** is now measured, and §6.2 says so with a pointer
to the record. The reopen, if any, is argued on `#4051` / `#13082` phase 2 with these figures.

**Adoption cost, measured from the artifacts** (no vendor page was reachable on the measuring box):
`ts_rank` is built-in — no extension, no worker, no restart-level config, a **64 KB** GIN index.
`pg_search` needs `shared_preload_libraries`, is **AGPL-3.0-or-later**, and its index is **3.13 MB**
(48×) for no head-quality gain over `ts_rank`. Evidence: `docs/evidence/13099-text-gate/`.

---

## `#12186` — M2 measured: the window's cost is its eviction, and it is silent

§9 M2 asks whether a windowed graph loses retrieval quality, and §7 arms the window only on its answer.
The measurement is `docs/evidence/12186-m2-windowed-quality/` — instrument `tools/window_quality_m2.py`,
1000 `:Object` names and 786 `:Point` claims from this box's capture corpus, 300 fixed queries per arm,
five coverages under two eviction policies.

**On entity names the cost is exactly the eviction share and nothing compensates.** `found@deep` equals
the resident share in **every windowed `:Object` arm of both policies**; the five `tail` arms read
91.3 / 77.3 / 51.7 / 23.3 / 8.7 and the `random` arms read their **own** shares
91.7 / 76.3 / 49.7 / 25.3 / 12.0, so what the window keeps it can still find, and what it demotes it
never returns (`found_deep_of_demoted` is 0.0 in every **windowed** arm of both populations).
⛔ Those `:Object` figures describe `#11901`'s deliberately class-stratified oversample (60 % `definite`,
30 % `ident`, 10 % `path`) against a real mix the artifact holds at ~8.0 % / ~3.4 % — the shapes
`#11901` chose to make the curve readable, **not** a weighted estimate of production entity-name
retrieval. The bundle weights nothing and no row here is a production rate.
The identity is `:Object`-only: on `:Point` keeping 52.7 % finds only 41.0 %, because the
claim leg has a ceiling of its own — the loss there is the eviction PLUS a ranking weakness the window did
not cause (it coincides at exactly one `:Point` arm, `random@0.1`, and falls short in every other
windowed arm). At §8's own solo target — about
half the complete graph — `recall@3` falls 88.3 % → 46.0 % and the top-1-is-wrong rate rises
27.7 % → 41.0 %. On the claim population every demoted query returns **another claim**, never an empty
answer — ⛔ a property of the **LEG**, not a window effect: `:Point` `empty` is 0.0 in all 11 arms
including `complete`, so the rate is 100 % for any demoted subset by arithmetic, and the complete graph
already reads 93.0 % `misbind@1`.

**Why the answer is architectural rather than statistical.** A `db.idx.fulltext` index covers only the
nodes the graph holds, and the record does not serve set-shaped reads — `record_serve.ANCHORED_READS` marks
`whole-graph` and `anchored-neighbours` as not served. So §7's seed-materialisation repair has nothing to
work on for a demoted candidate. The vector leg escapes this once it moves to Postgres (§3); the text leg
does not, because §6.2's decision item 2 keeps it in the graph.

**Two things the measurement changed that are not numbers.** The choice of *static* eviction order is not
a lever: the two orders differ by at most 5.0 points on any gated metric, in either direction (recall@3
46.0 vs 46.3 at 0.50; the cold-tail order ahead on some cells and **worse by 5.0 points at 0.25**),
against a collapse of **42.0 to 50.3 points** at that target size depending on the order — so neither
re-ranking moves the
ceiling, which is what the verdict needs
and is *not* the stronger claim that the orders are indistinguishable. ⛔ That
order is a **static popularity** count, not recency (this corpus has no read ledger), so §7's *recency*
claim was **not** re-tested and is not overturned here. And a
demotion must invalidate `fallback_snapshot`, whose 60 s
corpus is cleared by `SDK._mark_dirty` but not by a bare Cypher `DELETE`: without it the degraded path keeps
serving the demoted corpus, which this instrument caught as recall above the arm's own resident share.

What this does **not** decide: the vector leg (unarmed in this run, so every figure is the lexical leg's),
the byte-vs-node question (§8 budgets bytes, coverage here is a node share), and the lexical leg's own low
ceiling on claims — M3's ranking matter, measured there and not re-run here.

## `#13220` — 2026-11-17: V2 and V3's index half are placement answers, and §3's coverage now says which labels that is

**The ruling, verbatim:** *"if indexing improves search for Object/Subject/Event of course we want that,
and we just need it in Postgres and the search aligned with that and everything armed and working"* …
*"we should likely also index source also in postgres"*. It is recorded on `#13220` with the measurement
it was asked against.

**Why it belongs in THIS document.** V1's own decision block — at the head of this document, above §1 —
settles where the vector index lives and
explicitly not which labels carry one — so when the owner answers the label question as a *placement*
answer (*"just … in Postgres and the search aligned with that"*), the canonical home for it is the search
document, exactly as `#4051`'s 2026-11-07 ruling was. **What this entry adds to the design document is a
decision block after the 2026-11-07 one — carrying the per-label gate table and the four-condition
arming paragraph it ends in — and one row of §3:** the vector-search row now names the served
set (`Point`, `Event`, `Object`, `Source`, `Subject`, and the `Document` rows that are `:Source`'s other kind) instead of leaving coverage unsaid. ⛔ **No mechanism
moved:** §6.1's filtered-ANN guard, §6.2's decision that the text index stays in FalkorDB, and §1.5's
phase split are all untouched, because the ruling changes *which rows* the index answers, not *which
engine* answers.

**The load-bearing distinction, recorded so it is not flattened later.** `#11901` closed the index half as
*"not earned"* on a 390 s sweep, and that verdict is **about FalkorDB**: `CALL db.indexes()` showed a
single vector index (`Point`), so indexing the other served labels meant four further resident HNSW
structures — five total — on provisioned RAM with no eviction (`STORAGE-ARCHITECTURE.md` §12.1c).
**The record's mechanism is an `embedding_vec` column plus a pgvector index on the table that owns the
rows — the shape `public.point`/`public.source`/`public.event` already carry** — which is the whole
reason §12.1c's per-label argument, about
permanently resident structures on the most expensive resource we have, does not transfer to them. **The second distinction is the KEEP ruling, and it has moved once since it was made.** Indexing
`:Object`/`:Subject` in Postgres is **compatible** with the 2026-11-10 ruling that forbids removing their
graph-side embeddings; ⛔ **but that ruling was itself SUPERSEDED for the GRAPH COPY on 2026-11-17**
(owner, `#11395` comment `6956793300`, with its `OVERRIDES:` line): the graph copy **may** be removed and
its write stopped **once the record holds and indexes those vectors** — record first (`#13220` step 1),
graph removal second, in a later release, and never before (removing it early would delete the only copy,
the failure the original ruling existed to prevent). `:Event`/`:Source` were never named by that ruling,
so their graph copies were already retirable once the record serves them. ⛔ **SUPERSEDED 2026-11-17
(`#13350`, the entry below):** no owner ruling makes them retirable — comment `6956793300` authorises
retirement for `:Object`/`:Subject` and no other label — and the design document now says so.

**What the design document does NOT gain, on purpose.** No delivery state and no census: the per-label
vector population is still `tools/dod_5090_probe.py --census`'s to measure on a named graph, the probe's
docstring still forbids re-quoting the 2026-09-24 count, and `#13108`'s read-time measurement still carries
its own scope (it decided nothing and created no index). The work the ruling orders — columns and writer,
the record-served dense leg through `record_vector.LABEL_SERVED`, the retirements, the arming — is
`#13220`'s and is not this document's narration.

## 2026-11-17 — the KEEP ruling's *status* words restated: its prohibition is today's operative rule, and the GRAPH-COPY reopen is recorded as the ruling that governs removal (`#13350`)

Applies an owner instruction of 2026-11-17: the **INDEX** half stays *decided, implementation in flight — `#13220`*,
and the 2026-11-10 KEEP ruling's prohibition — `:Object`/`:Subject` keep their stored embedding, and no pass may
remove it or stop writing it — is kept intact and is what binds **today**, because the later ruling's own condition
is unmet.

**What changed is not only status wording — one content claim moved with it.** The decision block at the head of
the document — not a §3 paragraph; §3's change is the vector-search table row — had replaced the reopen's own
statement with a bare "the KEEP ruling is in force today" (**that bare sentence was an intermediate revision of THIS
PR** — absent from this PR's base, `#13350`'s merge `d392ac79aa`'s first parent:
`git grep -F "the KEEP ruling is in force today" d392ac79aa^ -- docs/architecture/SEARCH-ARCHITECTURE.md` → 0 hits —
and not the document's prior wording). That is not what the record says: `#11395` comment
`6956793300` is itself headed **"SUPERSEDED 2026-11-17 — the KEEP ruling's *reason* no longer holds, so the graph
copy retires"** and carries an `OVERRIDES:` line. The block **restores** the base's naming of that second ruling
as the one that governs removal — `git grep -F "GRAPH COPY only" d392ac79aa^ -- docs/architecture/SEARCH-ARCHITECTURE.md`
→ hits there — with its **GRAPH COPY only** scope and its `OVERRIDES:` citation, and **adds** the two-rules split,
which the base does not carry (`git grep -F "Two rules, stated as two" d392ac79aa^ -- docs/architecture/SEARCH-ARCHITECTURE.md`
→ 0 hits) — so the operative effect is unchanged (nothing may be removed or stop being written while `#13220`
is in flight, and never in the same release) and both owner statements are recorded rather than one cancelling the
other.

**The content claim that moved:** `origin/main`'s block ended *"`:Event`/`:Source` were never named by the KEEP
ruling, and were already retirable once the record serves them"* — a permission no owner ruling grants, since
comment `6956793300` authorises retirement for `:Object`/`:Subject` and no other label. The block now says the pair
is *"neither protected by the KEEP ruling nor retired by this second one"*, and states the record-first condition as
**SEQUENCING** — addressed to every label, scoping none — kept separate from the label-scoped authorisation to
retire. The entry above records the old sentence; this entry supersedes it.

⛔ **Why this entry exists.** `origin/main`'s decision block carried the reopen explicitly — *"The same day's SECOND
ruling reopens the KEEP half — for the GRAPH COPY only … `OVERRIDES:` recorded there"* — and this change replaced it
with a sentence that kept the condition but dropped the reopen's status, its scope and its `OVERRIDES:` citation.
The reopen's statement is restored with all three. This entry is the record that the wording moved **and that the
`:Event`/`:Source` permission sentence changed with it.** **No measurement was re-read and no figure was retired.**

## `#13422` — 2026-11-18: the `:Object`/`:Subject` read half landed, and the cage came from the record's FOLD rather than its view

**What changed.** `record_vector.LABEL_SERVED` gained `object` and `subject`, and the dense leg applies `#689`'s
terminal-status exclusion to them. The estimate this document carried — that the gate opened only when `#10193`
(the app role's SELECT on the procedural views) AND `#13335` (the views' two-value vocabulary) both closed — was
**wrong, and wrong in a way worth recording**: it named the SQL VIEW as the only record-side path to a status, so it
made two open issues look like preconditions of a read that never touches them.

**Why the view was the wrong path.** `#13335` is a real defect in `public.object_current_status`, which folds
`superseded_by`/`superseded_at` and nothing else. But the record's status fold is not the view:
`cache_projection.apply_event_stream_folds` replays `public.journal_event` — the record's own append-only truth,
on layer tables that carry their own SELECT policy, so no grant is needed — through `status_folds`' vocabulary and
the same per-event terminal latch the live graph fold applies, including the `EntityMutated` `state.status` carrier
`#9974` restored. All four terminal words were therefore expressible **before either issue closed**.

**What the leg does now.** `record_vector._folded_terminal_keys` builds the label's `RecordNode`s through
`cache_projection.node_from_row` (which applies `DERIVED_FROM_RECORD`'s carrier fold) and folds the tenant's
status-bearing events over them, then the kNN excludes the fold's terminal keys by `t.<id_column> <> ALL(%s)`. The
same prefetch returns the shortfall assertion's `available` figure, so the cage costs two extra statements and not a
fourth. Past `_FOLDED_TERMINAL_KEY_BUDGET` keys the leg RAISES `RecordVectorDeclined`, so the caller names the
fallback (`#4999`) and hands the graph branch only what is left of the read (`#11691`); a plain `None` — the
contract's *"no record table serves this label"* — is taken silently, which is why the first cut was wrong.

**What the review changed, and why it is recorded here rather than tidied away.** Three of the first cut's claims were
wrong in ways that only a reviewer could see (`#13466`):

- the prefetch was `SELECT *`, which materialises `embedding` (jsonb, **4.6–7.7 KB/row** for a 384-element array,
  measured with `pg_column_size`) and the generated `embedding_vec` (a FIXED `vector(384)`, **1,544 B/row**) that the
  fold never reads. **Measured: ~2.0 s on 8,000 `object` rows**, against **~0.19 s** for the payload-free projection
  (27–47 ms of it the SELECT, the rest the fold) — so at the folded arm's per-statement share the leg could never
  answer on any tenant of realistic size, while presenting a bind budget it could never reach. ⚠️ Three DIFFERENT
  measurements, kept apart because a summary that merges two of them is how the wrong cost model survived review
  twice: the 8,000-row totals are the lane's, the per-row byte figures are the reviewer's and were reproduced on a
  second container (`vector(384)` = 1,544 B exactly; jsonb 4,616 B short-repr → 7,688 B full precision, with the
  lane's real rows at 4,845 B);
- the leg issues **three** data statements against the column arm's two, so dividing the read budget by
  `RECORD_LEG_STATEMENTS_PER_READ` let it spend the WHOLE read and hand the graph branch a floored `1 ms` — the
  `#11691` reserve silently did not hold for these two labels. The folded arm now divides by one MORE than it issues;
- the over-budget decline returned `None`, which the caller takes SILENTLY (it is the contract's *"no record table
  serves this label"*), so the fallback was recorded as a plain `scan` and the graph branch got the full budget again
  (`#4999`, `#11691`). It now raises `RecordVectorDeclined`.

⛔ **The residual that is still true, and the reason this is a read half rather than the whole answer** — restated,
because the first version of this paragraph named the wrong mechanism (`#13466` cycle 2, P3): the fold's Python work
costs ~0.14 s on 8,000 nodes and is bounded by **nothing in the leg** — `statement_timeout` bounds STATEMENTS, and no
arithmetic in `record_vector` bounds the work BETWEEN them. At the measured 0.19 s inside a 500 ms read the leg
ANSWERS; a tenant large enough for the fold to overrun what is left of the read does not get a `scan_fallback` but the
fused read's collector expiring on its own deadline and DISCARDING the rows as `timeout` — the `#11691` outcome, one
layer up. The cost is removed by materialising the fold; narrowing the node read per target is exactly where a missed
path becomes a `#689` leak, so it is filed rather than attempted.

**What did NOT change, and must not be read as changed.** `cutover_armed()` is still OFF by default, so **the graph
leg answers for these two labels today** exactly as it did; the graph copy is still written and still read, because
removing it is the second step of the `#11395` comment `6956793300` ruling (record first, graph removal second, never
the same release) and is not this issue. `#13335` and `#10193` remain open on their own merits — closing them removes
this leg's prefetch COST, not its CORRECTNESS.

**In `SEARCH-ARCHITECTURE.md`:** the read-half paragraph (restated as paid, with the residual named rather than
retired), the `Object` and `Subject` rows of the per-label gate table, and the arming-order sentence that called the
last two *"gated on the status fold"*. **No measurement was re-read and no figure was retired.** The live arm is
`tests/test_11642_vector_move_live.py` (the file `test-record-postgres` runs) on `pgvector/pgvector:pg16`; the
structural pins are `tests/test_13422_object_subject_dense_leg.py`.
