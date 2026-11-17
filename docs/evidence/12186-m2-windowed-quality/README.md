---
title: "#12186 M2 — retrieval quality under a windowed vs complete graph (the §7 arming gate)"
type: evidence
domain: platform
doc_status: live
created: 2026-11-17
subjects.team: epistemic-team
aboutSubjects: cache window, residency, fulltext retrieval, degraded-fallback snapshot
aboutObjects: "#12186, SEARCH-ARCHITECTURE.md §7, SEARCH-ARCHITECTURE.md §9, #11901, #12349, #10976"
---

# M2 — retrieval quality under a windowed vs complete graph (2026-11-17)

**Verdict: §7's window is NOT SAFE AS SPECIFIED — it must not be armed.**
On the **text leg** — the one §6.2 keeps in the graph, and the only leg this run exercised — the
window's retrieval cost is **the eviction itself, with nothing compensating**: in a run that invalidates
the degraded snapshot after each demotion, a demoted node is never
handed back as a candidate at any rank, the caller is never told a store was partial, and on the claim
population a demoted query returns a **different claim** rather than an empty answer. ⛔ **That zero is
this configuration's, not a guarantee the shipped path makes**: `tortoise/cache_evictor.py::_delete_nodes`
issues a bare `DETACH DELETE` and never invalidates `fallback_snapshot`, so today's evictor leaves a
demoted node returnable for up to 60 s (Finding 6, filed as **#13123**). ⛔ **And "Cost equals
the eviction share" is a `:Object` identity, not a statement about the text leg as a whole.**
`found@deep` equals the resident share in every `:Object` arm, and on `:Point` it merely **coincides at
one arm** (`random@0.1`, 0.11 == 0.11) while every other windowed `:Point` arm falls short — where the
loss is the eviction **plus** a ranking weakness the window did not cause, and the two must not be added
into one number (Finding 2).
At §8's own target size (≈50 % of the complete graph) lexical recall@3 on entity names falls
**88.3 % → 46.0 %** and the wrong-answer rate at rank 1 rises **27.7 % → 41.0 %**.

This bundle is a measurement, not a proposal. No threshold was tuned; the only code added is the
instrument (`tools/window_quality_m2.py`).

- **Artifact:** [`measurement.json`](./measurement.json)
- **Instrument:** `tools/window_quality_m2.py` · **git head:** `8762578f8c70`
- **Corpus:** `~/.tortoise/session-events/*.jsonl` — 22/22 files, 653 lines, 1 parse error
- **Retrieval mode:** `keyword_only` (no `embeddings` extra on this box) — a figure about the
  **lexical** leg, which is precisely the leg §6.2 keeps in the graph. It is **not** a figure about
  the fused production read, and no such claim is made.

---

## Why this is a gate and not a nice-to-have

`SEARCH-ARCHITECTURE.md` §9 M2 asks for *"retrieval quality under a windowed vs complete graph, on a
fixed query set"* and §7 says the window is *"armed only after M2"*. §7 states the honesty item
plainly: **the bounded-cache literature measures latency and hit rate, not answer quality, and
whether a windowed graph harms retrieval quality is answered nowhere in that literature.** There is no
published number to import — which is what makes this record the measurement rather than a
transcription. (§7 said *"unmeasured anywhere"* until this PR; that wording is retired, because §9
measures it and an absolute like that is refuted by the artifact it introduces.)

**The leg that decides it is the text leg, and that is the whole architectural finding.** §3 splits
the read surface:

| leg | home | bounded by a graph window? |
|---|---|---|
| vector ANN | **Postgres** (moves, §3/#11642) | **no** — the record holds the whole population |
| **text** (`db.idx.fulltext`, 5 labels) | **FalkorDB** (stays, §6.2) | **YES — by construction** |
| multi-hop traversal | FalkorDB | **yes** |
| anchored reads (by id/key) | Postgres (`record_serve.ANCHORED_READS`) | no |

A fulltext index is maintained **by the graph over the nodes the graph holds**. Demote a node and
the index stops covering it. §7's repair — *"a walk materialises its seeds first — search returns
ids from Postgres"* — is sound only for a candidate generator that reads the record, and
`db.idx.fulltext` is not one: **it cannot return an id for a node it does not index, so there is
nothing to materialise.** This is confirmed in code, not inferred:
`record_serve.ANCHORED_READS` marks `whole-graph` **NOT_SERVED** (*"the 377 sites that name no
node"*) and `anchored-neighbours` **NOT_SERVED**. Set-shaped search and the walk are graph-only.

---

## What was measured

Two populations, because the window does not demote one kind of node:

- **`:Point`** — 786 nodes built from real captured turn text through the real projection
  (`_upsert_point_props`). **This is the population the window demotes** — §8 sizes the target at
  ≈77 MB against an ≈150 MB complete graph, and those bytes are claims/structure, not the entity
  dictionary.
- **`:Object`** — 1000 entity names from the same corpus via `candidate_recall_11901.harvest`
  (imported, not copied), the capture-prior population `#11901` measured.
  ⚠️ **This is `#11901`'s deliberately class-stratified oversample** — 60 % `definite`, 30 %
  `ident`, 10 % `path` — built so the minority shapes have enough names to measure, while this
  corpus's real mix is ~8.0 % ident and ~3.4 % path (`names_harvested`: 12,557 and 5,314 of 156,449). `build_population`'s own docstring says the
  composite "is **not** an estimate of the production mix", and that carries here: the `:Object`
  figures below are the shape `#11901` chose, not a weighted estimate of production entity-name
  retrieval. Nothing in this bundle weights them, and no row is presented as a production rate. It is
  kept because it makes M2's curve comparable with the recall figures the epic already carries.

⚠️ **The `:Point` population's own drop funnel — 900 requested, 786 built** (#13126 review P2-3).
`--points 900` is a REQUEST, not a count: 900 texts are taken into the population builder and 786
Points come out, because four filters can lose a text — `too_short` (a turn under 80 characters),
`no_span` (no valid 5-token span), `sanitised_out` (a span the production sanitiser collapses below
three terms) and `duplicate_span` (a span an earlier Point already claimed; this corpus repeats
boilerplate verbatim). The instrument records that funnel under `corpus.point_build`, and for **this**
run the block carries `split_recorded: false` with the 114 lost texts left `unattributed`: the committed
artifact was produced before the accounting existed, and the split is neither guessed here nor
back-filled into the block. A run of the current instrument reports the four counts instead. The split
changes no published rate — every arm is measured on the 786 built Points — but it is the number that
says *which* texts the population excludes, which is why it is recorded rather than left to be inferred
from `params.points` and `populations.point.nodes`.

Each population is measured **complete**, then at coverages 0.90 / 0.75 / 0.50 / 0.25 / 0.10 under
**two** eviction policies: `tail` (coldest/most-boilerplate first — a **static popularity** count; §7
describes a *recency* policy, which this corpus cannot supply and this run did **not** test — see
Finding 4)
and `random` (a seeded control). **300 queries per arm, sampled once and reused by every arm**, so
arms compare graph states and not populations. Every query makes **one** read at depth 50: `recall@k`
is a slice of it and `found@deep` is the whole of it, which separates *"the leg cannot rank it"* from
*"the window does not hold it"*.

⛔ **How the queries are built — and the `:Point` set is SELF-RETRIEVAL, which bounds every claim
about it.** The corpus carries no labelled query set, so:

- **`:Object` queries** are entity-name queries sampled from the same names the population is built
  from (`#11901`'s construction, imported rather than re-invented).
- **`:Point` queries are a contiguous span of that Point's own content** — i.e. *self-retrieval*. That
  measures the retrieval question *"can the claim containing this phrase be found"*, and it is
  **NOT a measure of answer quality**: a real user query is not a verbatim span of the document it
  should match, so the `:Point` rates here (`recall@3` 15.7 %, `found@deep` 71.3 % complete) are an
  UPPER bound on this leg's usefulness and must not be read as end-to-end search quality.

The instrument declares this under its "what is real and what is not" header; neither record said it,
which left the single caveat a reader needs to interpret the `:Point` table unstated.

### Three invariants, all enforced — and two of them fired during development

(Below them, a fourth bullet records a *property* the control depends on rather than a refusal — it is
a constant in the instrument, and it is not counted among the invariants.)

1. **`recall@k <= resident_share` and `found@deep <= resident_share`** — a demoted node cannot be
   returned at a rank.
2. **The graph holds exactly the intended resident set** after eviction.
3. **`found_deep_of_demoted == 0` in every windowed arm** — no demoted node is returned at **any rank
   of the depth-50 read**. This is the strong form of (1) and the one the verdict rests on: `found@deep`
   is a rate over ALL queries, so on a population whose complete-graph ceiling is below 1.0 (`:Point`:
   **71.3 %** — 28.7 % of claim canonicals are unreachable even with the whole graph resident) a leak
   of demoted queries sits *under* the aggregate bound and passes (1).
   ⛔ Scoped two ways, deliberately: **windowed** arms only, because the `complete` arm has no demoted
   queries so its 0 is a zero denominator rather than an observation; and **of the depth-50 read**
   only, because that one read at `DEEP_LIMIT` is all the check can observe — a demoted node at rank 51+
   would be invisible to it, so "any rank" unbounded would claim more than the instrument measures.
4. The query-sample seed and the `random` eviction seed are **independent** (`EVICT_SEED_OFFSET`).
   They are permutations of the same list; one shared seed made the retained suffix and the sampled
   prefix disjoint by construction, and the first run reported `resident_share` 0.667 / 0.167 / 0.000
   at coverage 0.90 / 0.75 / 0.50 — true about the graph, useless as a control.
   ⛔ Those three numbers are a **development observation from a superseded run and are NOT in
   `measurement.json`**; they are quoted to show what the invariant caught, not as evidence a reader
   can check. The artifact carries the fixed instrument's numbers only.

⛔ **Invariant 1 caught a real defect in the product, not in the instrument** — the symptom was the
aggregate bound being crossed, which is (1)'s refusal; (2)'s exact-set check was satisfied, because the
graph *did* hold the intended set and the stale rows came from the snapshot, outside the graph.
`fallback_snapshot`
keeps a 60 s in-memory copy of the whole corpus and serves it on the **degraded** path. It is
invalidated by `SDK._mark_dirty`, which every normal write surface calls — and which a raw Cypher
`DELETE` does **not**. The first eviction therefore left the snapshot serving the demoted corpus and
an arm reported `recall@10` **0.1133** against `resident_share` **0.1100** (likewise a development
observation from a superseded run, not a figure in `measurement.json` — the defect is reproducible by
deleting the `invalidate_degraded_snapshot()` call, which is the reproduction, not a re-quote). See
[Finding 6](#6-a-demotion-must-invalidate-the-degraded-fallback-snapshot-or-it-does-not-happen).

---

## Results

### `:Object` — the capture-prior population (strong baseline)

| arm | resident | found@deep | recall@3 | recall@5 | recall@15 | misbind@1 | wrong-not-miss¹ | silent-zero¹ |
|---|---|---|---|---|---|---|---|---|
| complete | **100.0** | **100.0** | **88.3** | **95.0** | **98.7** | **27.7** | **0.0** | **0.0** |
| tail@0.90 | 91.3 | 91.3 | 81.0 | 86.3 | 90.0 | 29.0 | 53.8 | 46.2 |
| tail@0.75 | 77.3 | 77.3 | 69.3 | 71.7 | 76.3 | 34.3 | 50.0 | 50.0 |
| tail@0.5 | 51.7 | 51.7 | 46.0 | 48.3 | 51.3 | 41.0 | 53.1 | 46.9 |
| tail@0.25 | 23.3 | 23.3 | 20.3 | 21.7 | 23.3 | 46.0 | 49.6 | 50.4 |
| tail@0.10 | 8.7 | 8.7 | 8.7 | 8.7 | 8.7 | 44.0 | 46.4 | 53.6 |
| random@0.90 | 91.7 | 91.7 | 81.7 | 86.3 | 90.7 | 29.0 | 56.0 | 44.0 |
| random@0.75 | 76.3 | 76.3 | 70.7 | 73.3 | 76.0 | 33.3 | 69.0 | 31.0 |
| random@0.5 | 49.7 | 49.7 | 46.3 | 48.0 | 49.7 | 43.0 | 63.6 | 36.4 |
| random@0.25 | 25.3 | 25.3 | 25.3 | 25.3 | 25.3 | 42.7 | 54.5 | 45.5 |
| random@0.10 | 12.0 | 12.0 | 12.0 | 12.0 | 12.0 | 37.0 | 42.0 | 58.0 |

¹ scoped to the **demoted** queries, not to the query set.

### `:Point` — the population the window demotes

| arm | resident | found@deep | recall@3 | recall@5 | recall@10 | recall@15 | misbind@1 | wrong-not-miss¹ | silent-zero¹ |
|---|---|---|---|---|---|---|---|---|---|
| complete | **100.0** | **71.3** | **15.7** | **22.0** | **33.0** | **40.7** | **93.0** | **0.0** | **0.0** |
| tail@0.90 | 90.0 | 64.7 | 12.7 | 18.0 | 31.0 | 39.3 | 94.3 | 100.0 | 0.0 |
| tail@0.75 | 74.3 | 55.3 | 11.3 | 16.0 | 25.3 | 32.7 | 94.3 | 100.0 | 0.0 |
| tail@0.5 | 52.7 | 41.0 | 8.3 | 13.3 | 21.7 | 25.7 | 95.7 | 100.0 | 0.0 |
| tail@0.25 | 24.7 | 22.0 | 5.0 | 8.0 | 11.0 | 13.3 | 98.7 | 100.0 | 0.0 |
| tail@0.10 | 10.0 | 9.7 | 6.7 | 7.7 | 8.3 | 8.3 | 97.3 | 100.0 | 0.0 |
| random@0.90 | 85.3 | 63.7 | 14.7 | 21.3 | 28.7 | 37.0 | 92.7 | 100.0 | 0.0 |
| random@0.75 | 72.7 | 55.0 | 12.3 | 17.7 | 25.7 | 31.7 | 93.0 | 100.0 | 0.0 |
| random@0.5 | 49.3 | 40.0 | 10.0 | 14.0 | 19.3 | 24.0 | 94.3 | 100.0 | 0.0 |
| random@0.25 | 25.0 | 23.7 | 8.7 | 11.0 | 15.0 | 17.3 | 95.7 | 100.0 | 0.0 |
| random@0.10 | 11.0 | 11.0 | 5.3 | 6.3 | 10.0 | 10.0 | 97.3 | 100.0 | 0.0 |

---

## Findings

### 1. The window's retrieval cost is EXACTLY its eviction share — nothing compensates

For `:Object`, `found@deep` equals `resident_share` in **every** windowed arm, to the decimal:
91.3 / 77.3 / 51.7 / 23.3 / 8.7 and 91.7 / 76.3 / 49.7 / 25.3 / 12.0. Read both ways:

- **every resident canonical is still findable at depth 50** — on `:Object` the window does no
  collateral damage to what it keeps. The `random` arms read 91.7 / 76.3 / 49.7 / 25.3 / 12.0, which are
  *their own* resident shares (not the tail series above — 77.3 vs 76.3, 51.7 vs 49.7, 8.7 vs 12.0);
- **no demoted canonical is ever returned** — `found_deep_of_demoted` is **0.0** in every **windowed**
  arm of both populations. ⛔ Scoped to the windowed arms deliberately: `summarise._rate` returns 0.0
  on a zero denominator, and the `complete` arm has no demoted queries, so its 0.0 is arithmetic, not
  an observation — 20 arms measured this, not 22. No record repair rescues a demoted candidate (`record_serve` cannot serve the set read
  it would need), and no leg in this run returns one. ⛔ **The single qualifier is the degraded
  snapshot**, and what it qualifies is *how* the zero is obtained. `fallback_snapshot` keeps a 60 s
  in-memory corpus, so it can serve a node the graph no longer holds for up to 60 s after a delete
  (Finding 6) — and `legs_seen` records only that the tier **ran** (the submitted predicate, whether or
  not it was degraded; it is not a record of the tier serving anything). Its corpus is
  `MATCH (n:Point)` against the live graph, which bounds it to the nodes resident **when it was built**:
  it therefore *cannot* hold a node demoted before the build, but it *can and does* serve one demoted
  **after** it, until `_mark_dirty` or the 60 s TTL expires. That is exactly why the zero rests on the
  instrument's explicit `invalidate_degraded_snapshot()` call (Finding 6) and not on the corpus source —
  and why "there is no fallback" would be false.

⚠️ **`:Point` is not an instance of that bullet.** There `found@deep` falls below `resident_share`
(keeping 52.7 % finds 41.0 %), because the claim leg has a ceiling of its own (Finding 7). On claims
the loss is the eviction **plus** a ranking weakness the window did not cause.

That identity is the finding. It means no amount of tuning the window's *shape* recovers the loss;
only retaining the node does.

### 2. At §8's own target size, lexical recall@3 on entity names falls 88.3 % → 46.0 %

§8 derives the solo window target as **≈77 MB against an ≈150 MB complete graph** — around half.
At `tail@0.50`: recall@3 **88.3 % → 46.0 %** (−42.3 points), recall@15 98.7 % → 51.3 %. On the claim
population `found@deep` falls **71.3 % → 41.0 %**, and recall@3 15.7 % → 8.3 %.

⚠️ **This is the answer to M2's question, and it is not a marginal one.** The window exists to cut a
complete graph from 193 % of the 30 % budget to it (§7, §8.1); the reduction it needs is the
reduction that destroys lexical retrieval.

### 3. The window does not just lose answers — it raises the WRONG-answer rate

`misbind@1` (top-1 is a different node) **rises** as coverage falls:
**27.7 % → 41.0 %** (tail@0.50) → 46.0 % (tail@0.25) on `:Object`. A miss costs a new node; a
misbind is how the epic's own headline failure — the **duplicate-noun rate** (epic #12186,
"Capstone") — gets minted downstream in link-before-create.

On `:Point` this is total: **`wrong-not-miss` is 100.0 % in every windowed arm.** A demoted claim
query never comes back empty; it comes back with **some other claim** at rank 1. For claims, the
window's failure mode is not "no results" — it is a confident wrong answer.

⛔ **Read that pair as a property of the leg, not as a measured delta.** `:Point` `empty` is **0.0 in
all 11 arms, `complete` included**, so `silent_zero ≡ 0` and — given `found_deep_of_demoted = 0` —
`wrong_not_miss ≡ 1` for *any* demoted subset: the two rates are an arithmetic consequence of a leg
that always returns at least one row, not a window effect. The claim they support is still the
important one and it survives: a caller asking for a demoted claim is **wrong**, never told so. But the
same sentence could be written about the complete graph (93.0 % `misbind@1`), so it must not be
presented as a difference the window makes. On `:Object`, where `empty` ranges **3.7–51.0 %**, the same
two rates *are* informative.

### 4. No eviction policy protects retrieval quality

`tail` (evict the least-referenced/most-boilerplate first) is **not a lever**, and the honest form is a
**comparison of scales**, not a claim that the two never differ. On every metric this record gates on,
the two orders stay within **5.0 points** of each other (recall@3 **46.0 vs 46.3** at 0.50; 81.0 vs
81.7 at 0.90), and `tail` is the **worse** of the two where the resident set is small — **20.3 vs 25.3**
at 0.25, **8.7 vs 12.0** at 0.10, that 5.0-point gap being 5× the 1.0 pp `recall@k` band.
⛔ **It is NOT true that `tail` never buys anything back, and an earlier cut of this record said so.**
It is ahead on a few cells by more than the noise: up to **2.00 pp on `found_deep` at 0.50** (that
metric's band is 0.33 pp) and **2.33 pp on `:Point` `recall@10` at 0.90**. What makes the policy choice
irrelevant is the SCALE of the alternative, not an absence of difference: the cold-tail arm loses
**42.3 to 48.3 points** to the eviction and the random control **42.0 to 50.3** — so a 5.0-point edge is
barely a tenth of the loss and changes nothing
about whether a demoted node is retrievable — it is not, whichever reason selected it. Per resident
query the two are 87.1 % vs 100.0 % at 0.25, and `recall@k` is bounded by the resident share in every
arm of both policies.

⛔ **What `tail` actually orders on, stated precisely, because it bounds this finding.** It is a
**static popularity count**, not recency: for an `:Object`, the number of session records its name
appeared in; for a `:Point`, the number of query-relevant tokens in its content. This corpus carries
**no read ledger**, so no true recency/frequency-of-access policy could be built from it, and this
finding is therefore about the *static* axis — which is the axis §7 says recency beats. The honest
conclusion is narrower than "the policy does not matter":

- **a smarter *static* ranking cannot recover what the window costs** — the two static orders
  (referenced-first vs random) are **bounded by the same ceiling**: what the window keeps it can still
  find, and *a demoted node is unretrievable whatever reason selected it*. They differ by at most a few
  points in either direction — `tail` ahead on some cells, **worse by 5.0 points at 0.25** — against a
  collapse of **42.0 to 50.3 points** depending on the order, so read this as "neither re-ranking moves
  the ceiling by more than a fraction of the loss", never as "the two are the same" and never as "the two agree";
- **§7's actual claim — that *recency* beats static ranking — was NOT re-tested here** and this record
  does not overturn it on the retrieval axis. It refines it: recency may well pick a better 50 % to
  keep, and the curve above says the *kept* half then still loses exactly the evicted half. Whether
  recency also raises `found@deep` for the kept nodes on `:Point` is untested and is M3/M5's.

### 5. Every one of those losses is SILENT — §5 Q2 is violated, measured from the code

`signal` in the artifact is resolved from the code, not asserted:

```json
{"capture_prior_read_status": false, "capture_prior_truncation": false, "residency_marker_armed": false}
```

- `extractor_v2._fts_rows` — the capture path, the one place a wrong prior is **persisted** rather
  than merely displayed — passes neither `read_status_out` nor `_truncation_out`. It receives rows
  only.
- `TORTOISE_RESIDENCY` is **unarmed**, so no response carries a residency verdict.
- The two reason strings §7 requires (`degraded: "truncated_boundary"` /
  `degraded: "insufficient_graph"`) appear nowhere in the tree.

So the 46.9 % of demoted entity queries that came back empty under `tail@0.5` (36.4 % under
`random@0.5`) came back empty with
**no signal at all**, and §5's **Q2 — *"Nothing is silently invisible"*** — is violated for every one
of them. §5 ranks Q2 above latency, and Q1 (traversal completeness) above Q2:
`coalesce(ep_alpha, 1.0)` turns a demoted neighbour into a prior.

### 6. A demotion must invalidate the degraded-fallback snapshot, or it does not happen

`tortoise/fallback_snapshot.py` keeps a 60 s in-memory corpus snapshot and serves it on the
**degraded** retrieval path — the tier that runs precisely when the other strategies fail. It is
invalidated by `SDK._mark_dirty`, which every normal write surface calls **and a raw Cypher `DELETE`
does not**.

Measured: after a bare `DELETE`, an arm reported `recall@10` **0.1133** against
`resident_share` **0.1100** — a demoted node still served. With `sdk._mark_dirty([])` after each
eviction the same arms are exact. **This is a §7 pre-beta requirement, not a test detail**: the
window may not serve a node it has demoted, on any leg — and the degraded path is the one where it
matters most, because that path is active exactly when retrieval is already failing.

⚠️ The same gap exists in shipped code: `tortoise/cache_evictor.py::_delete_nodes` issues
`DETACH DELETE n` and never invalidates the snapshot, so the egress executor can delete a node and
leave it returnable for up to 60 s. Filed separately as **#13123** rather than fixed here — this
lane measures and does not change the eviction path.

### 7. The lexical leg's own ceiling is low on claims, independent of the window

`:Point` recall@3 is **15.7 %** and `found@deep` **71.3 %** on a **complete** graph. That is not a
window effect: 28.7 % of claim canonicals are unreachable at depth 50 with nothing evicted. It is a
**joint property of this query set and the leg's ranking**, and the two must not be conflated: the leg
OR-unions the query terms (`sparse.build_or_query`) and ranks by RediSearch score, applying **no
coordination factor and no length normalisation** (§6.2 records the same class), so a document holding
**every** query term can rank below documents holding only common subsets. The instrument also chose
these queries by a selectivity proxy the leg does not use (see `query_span`), and its adopted rule costs
~3 points of ceiling against the first-window rule it replaced (15.7 % vs 19.0 % on a complete graph —
⚠️ a **development observation from the superseded run and NOT in `measurement.json`**, quoted the same
way as the invariant's first-run numbers above, not as evidence a reader can check) —
so part of this ceiling is the **query set**, not the leg, and the leg's share is not separable here. ⚠️ This is **M3's** territory and M3 has already been measured and declined
(`#12349`); it is recorded here because it bounds what a *labelled* window could ever deliver.

---

## What this means for §7

**The window must not be armed on the strength of these numbers.** The decision §7 asks for is
*"does a windowed graph lose retrieval quality beyond an accepted bound"*, and the measured answer is
that the loss is **equal to the eviction on `:Object`, and on `:Point` the eviction plus the claim leg's
own ceiling** — and in both it is **unlabelled** — so any bound below "the whole evicted
share" is exceeded, and the bound cannot be met by choosing a better eviction policy (Finding 4).

There are three ways forward, and they are **architecture decisions, not tuning** (§10: *"M2 showing
a windowed graph loses retrieval quality beyond an accepted bound"* is a listed reopen):

1. **Move the text index to the record as well.** Then the candidate set is record-served and a graph
   window cannot shrink it. ⛔ This **contradicts a recorded decision** — §6.2 (decision item 2,
   *"text stays"*) — and M3 (`#12349`) declined the move on measurement. The route is a **reopen** in
   §6.2's own home, argued with this evidence, **not** an adoption.
2. **Exclude the searchable population from the window.** Keep `:Point`/`:Object`/`:Subject`/
   `:Event`/`:Source` resident and window only what is not search-indexed. That changes what the
   window is *for* and needs its own sizing against §8's 77 MB target — this bundle does not claim it
   meets it.
3. **Label every demoted answer and repair it from the record.** Requires the two §7 reason strings,
   the residency marker armed, and a record-served text read (`record_serve`'s `whole-graph` entry
   says that read does not exist). Until then, Finding 5 makes the window a silent-quality change in
   front of customers, which §1.5 exists to prevent.

**What M2 does NOT say.** It does not say the *vector* leg is bounded — it is not, once it moves
(`record_vector.ARMING_ENV` is unarmed at this head, so this run did not exercise it at all; the
`keyword_only` mode is declared in the artifact). It does not adjudicate Finding 7 — that is M3's
measurement and `#12349` already declined it. And it does not price anything: coverage is a NODE
share here, while §8 budgets BYTES.

## Reproducing

```bash
uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"
uv run python tools/window_quality_m2.py --points 900 --objects 1000 --sample 300 \
    --out /tmp/m2-windowed.json
# then compare — do NOT write over the committed artifact, which is what you compare against
uv run python -c "import json;print(sorted(json.load(open('/tmp/m2-windowed.json'))))"
```

⛔ `--out` deliberately points at a scratch path. Re-running over `measurement.json` would replace the
record — with your box's corpus, your `generated_at`, your graph prefix — and destroy the thing the
reproduction exists to compare against. The corpus is box-local (`~/.tortoise/session-events/*.jsonl`),
so an honest reproduction reports the **bound** below, not byte-identity.

Seeded given `--seed` (11901): the query sample and both eviction orders are fixed, the population
order is fixed, and **each arm is measured on its own freshly created graph** so an arm is a graph
state that can be described rather than a residue of the previous one. A run that violates either
invariant **refuses** (exit 3) instead of publishing a rate for a graph state that did not exist.

⚠️ **The reproducibility bound, measured, not assumed — and it is IN the artifact**, at
`reproducibility` in `measurement.json` (`runs_compared`, `found_deep_max_delta_pp`,
`recall_at_k_max_delta_pp`, `tie_counting_max_delta_pp`, `conclusions_rest_on_gap_pp`, and `why`).
**Three** runs of identical input were compared. The GATE metrics are stable — `found@deep` moved by at
most **0.33 pp** and `recall@k` by at most **1.0 pp** — so a single-run value of either may be quoted as
a magnitude. The TIE-COUNTING rates are **not** — they moved by up to **9.85 pp** (`empty`,
`misbind_at_1`, `wrong_not_miss`, `silent_zero`), because those count the handful of queries whose
*order among irrelevant rows* the leg breaks differently on engine-internal document ids. That is why
every arm is measured on its own fresh graph, and why **a tie-counting result is quoted as a direction
and an ordering only, never as a single-run magnitude**.

⛔ **The ratio, stated honestly, because the first version of this paragraph flattered it.** The
gaps the **verdict** rests on run **13.3 to 48.3 points** — the `27.7 % → 41.0 %` misbind move at the
floor (13.3) and `found@deep` at §8's target size at the top (48.3); the recall drops sit between them,
42.3 at depth 3 and 47.3 at depth 15. The artifact records that range under `conclusions_rest_on_gap_pp`
as `13.3 to 48.3`, and a pin RECOMPUTES it from those four rates, so the constant cannot certify itself.
So the tie band is up to **~3/4 of that floor** — *not* the "barely 1/8" an earlier cut claimed.
⚠️ **This is the VERDICT's range, not every claim in the record.** Two sub-findings rest on
much smaller gaps and must not be read as magnitudes: `tail`-vs-`random` separates by **5.0 points** at
coverage 0.25 (Finding 4 — a DIRECTION claim, deliberately not a magnitude), and the first-window rule
costs **~3 points** of ceiling against the adopted one (Finding 7 — a superseded run, not in the
artifact). The band is larger than either of those, which is exactly why neither is quoted as a number
to act on. **The verdict does not depend on any of them** — `found_deep_of_demoted == 0`
(in every **windowed** arm; the `complete` arm has no demoted queries) and `recall@k <= resident_share`
are *not* tie-counting quantities, they held in every run, and
they are what make the loss equal to the eviction **on `:Object`** (on `:Point` the loss
is the eviction plus the claim leg's own ceiling — Finding 2). A reader comparing two runs of their own should
expect the same split — the bound is a property of the instrument, and the artifact records it rather
than assuming it. And a reproducer should get the artifact's own block back: `tools/window_quality_m2.py`
emits it from `REPRODUCIBILITY_BOUND`, and `tests/test_window_quality_m2.py` pins the committed
artifact's block **equal** to that constant.

⛔ **What that pin is, stated exactly, because the earlier cut of this paragraph overclaimed it**
(#13126 review Finding 1). It is a **composition** check: the artifact's block and the instrument's
constant are the same values, so an edit to one that stales the other reds the suite. It is **not**
provenance evidence, and neither is `provenance.tool_sha256`: the digest is hand-updated whenever
`tools/window_quality_m2.py` changes, so it names the **current** bytes and never the bytes that
produced these numbers, and the `reproducibility` block (3 runs, 0.33 / 1.0 / 9.85 pp) is a
hand-maintained constant whose three source runs are **not** committed. Nothing in this bundle lets a
reader re-derive the band; re-running the instrument is what reproduces it, which is what the section
above asks a reader to do. The pin's job is to keep the two hand-maintained copies agreeing — a drift
control — and the record says so rather than calling it a check on the instrument's bytes.

The artifact also carries `provenance.tool_sha256` (that drift control on the instrument's current
bytes, not an attestation by the producing run), `corpus.point_build` (the `:Point` population's own
drop funnel — see "What was measured"), and a `cleanup` block that declares how many graphs a run
leaves behind (they are **not** dropped; on a lane container `test_lane.py down` is the disposal path).
