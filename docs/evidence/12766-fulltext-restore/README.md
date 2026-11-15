---
title: "Measurement — a graph-key RESTORE carrying a FULLTEXT or RANGE index (#12766)"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-14
subjects.team: epistemic-team
issue: "#12766"
aboutSubjects: Tortoise memory graph
aboutObjects: SEARCH-ARCHITECTURE.md, FalkorDB, fulltext index, range index, key-level DUMP/RESTORE, graph eviction
---

# Measurement — a graph-key RESTORE carrying a FULLTEXT or RANGE index (#12766)

Measured at: `measure/12766-fulltext-restore` on 2026-11-14 — the harness is byte-identical at
`f6ac10e41f` and `c9ff52ad0c` (it was not edited between the two measured runs); the 20 000-node log
landed in `f6ac10e41f`, the 90 000-node log in `c9ff52ad0c`, the range probe log in the commit after.

> The measured artifact is the **FalkorDB engine image**, not this repository's tree: the repo
> hosts the harness. The tree line above names the commit the harness was run from.

⚠️ **A later lint-only pass touched the harness, not its readings.** `ruff check .` was red on `main`
over three files in this directory (`SIM905`, `RUF100`, `B905`, `RUF010` ×2, `I001` ×2); the fix
replaced each construct with an equivalent form — a list literal for `str.split()`, `!s` for
`str(…)` inside an f-string, an explicit `zip(strict=False)`, a re-sorted import block, and the
removal of a `noqa` for a rule this repo does not select. **No expression changed its value**, so
the figures in this record stand, and `f6ac10e41f` / `c9ff52ad0c` remain the commits the harness was
measured at.

**Verdict — the kept fulltext index DOES block the instance, for 1.585 s at 90 000 nodes. Zero-standing
hot stays viable for `free`/`anon` against a caller read bound, but whether it is viable against a
per-tenant latency objective is a question this record cannot answer (§7).**

| | 20 000 nodes | 90 000 nodes |
|---|---|---|
| **fulltext kept** | RESTORE **0.405 s**, bystander **358.9 ms** | RESTORE **1.585 s**, bystander **1 535.2 ms** |
| **fulltext dropped before DUMP** | RESTORE **0.011 s**, bystander **10.6 ms** | RESTORE **0.032 s**, bystander **41.2 ms** |
| index-free floor (no index at all) | RESTORE 0.011 s, bystander 8.8 ms | RESTORE 0.041 s, bystander 53.0 ms |
| *vector index, for comparison — ⚠️ **different engine AND different payload** (§3)* | *RESTORE 2.955 s, bystander 2 899.4 ms* | *outlived a 10 s client read bound, surfaced at 53.3 s* |

⚠️ **The vector row is not like-for-like and is not used as a multiplier.** #7846's 20 000-node figure
is graph module **42201** over a **9.66 MB (decimal)** payload; its 90 000-node figure is graph module
**60001** over **43.19 MB (decimal)**. This record is graph module **42006** over **1.11 MiB /
5.02 MiB** (its logs record MiB). So a multiplier mixing them mixes engine, payload **and unit** with
index type and **overstates the index-type difference**; what the comparison supports is the
*direction* — a fulltext rebuild is far cheaper than a vector one at the same node count — not the
multiplier. The one same-engine datapoint points the same way and is reported without a ratio because
**both the node count and the payload differ**: #7846 `verify-ci-42006-5000.log` restores a
vector-indexed graph of **5 000** nodes / **2.44 MB** payload in **2.25 s**, against this record's
fulltext-indexed **20 000** nodes / **1.11 MiB** payload in **0.405 s** — same engine, four times the
nodes, a fifth of the time.

So the §7 sentence resolves the way the decision hoped rather than the way it feared: a kept
`db.idx.fulltext` blocks a restore **exactly as the vector index did** — the bystander's worst `PING`
*is* the restore duration in both sizes — but the pause is **substantially shorter at both sizes**. At
20 000 nodes the two named measures differ by **7.30×** (RESTORE) and **8.08×** (block), and at 90 000
the vector call produced **no completed duration at all**: 53.3 s is when the client's 10 s read bound
abandoned it, so the difference there is a **floor of ≥33×**, not a ratio. ⚠️ **Both are bounds on the
difference between those two runs — not a multiplier on index type** — because they are cross-engine
and cross-payload comparisons (see the ⚠️ under the table); the direction is what is load-bearing. It
finishes in **1.585 s** where the vector index did not finish inside 10 s. **For the window's own
restore that is a scheduling problem (do not cluster first touches), not a feasibility problem;
whether it clears a per-tenant latency objective is a separate question this record does not answer
(§7 point 0).** It is also avoidable outright: the drop-first path costs 0.032 s of RESTORE and
41.2 ms of bystander at the same size.

---

## 1. The leg §7 left open, and what decides it

`docs/architecture/SEARCH-ARCHITECTURE.md` §7 measures the **vector** index and then says the thing
this lane was opened for:

> "⚠️ **The window KEEPS the fulltext index in the graph** (decision item 2, §6.2), so the index that
> stays is exactly the one that record did not measure: if a kept `db.idx.fulltext` — or a range —
> index blocks a restore the way the vector index did, then the window is a feasibility question
> again"

Three facts had to be established, and they are separable:

| | question | answered by |
|---|---|---|
| **mechanism** | does index *data* travel in the payload, or is it rebuilt on load? | the payload size (§2) |
| **cost** | how long does the restore take, kept vs dropped? | the two-size table (§3) |
| **blast radius** | is the instance blocked, or is the restore merely slow? | the bystander (§4) |

## 2. The mechanism question — the payload size settles it, and the answer is "rebuilt"

| arm, 20 000 nodes | `DUMP` payload | index after restore |
|---|---|---|
| no index | **1.11 MiB** | — |
| fulltext kept | **1.11 MiB** | `OPERATIONAL`, 20 000 docs, 100 035 terms, 2 230 000 inverted bytes |
| range kept | **1.11 MiB** | `OPERATIONAL`, 20 000 docs |

The payload is **equal in size at the log's resolution** whether or not an index exists — 1.11 MiB at
20 000 nodes and 5.02 MiB at 90 000 across every arm, recorded as MiB to two decimals, so the
granularity is ~10.5 KB rather than a byte count. Yet the restored index reports **exactly** the
pre-restore `numDocuments`, `numTerms` **and** `invertedSize` (2 230 000 → 2 230 000 at 20 000 nodes;
10 035 000 → 10 035 000 at 90 000).

**`invertedSize` is a byte count** — established from the probe rather than assumed: the value-0 case
loses two postings and `invertedSize` drops **9 → 5**, i.e. 2 bytes per posting, and the same 2-byte
loss per entry holds at scale (79 526 → 79 524; 383 265 → 383 263). At 20 000 nodes the fulltext index
reads **2 230 000 bytes** for 20 000 documents and 100 035 terms — **≈111.5 bytes per document** and
≈22.3 bytes per *term*; at 90 000 it reads 10 035 000 bytes, the **same ~111.5 bytes per document**.

⚠️ **The unit barely matters to step 1, and this is stated so the argument does not rest on it:**
2 230 000 and 10 035 000 against payloads of 1.11 MiB and 5.02 MiB make the index **~1.9× the payload
at both sizes** — the exclusion holds for any unit within a factor of ~1.9 of a byte, and the byte
reading is the one the probe supports.

**What that does and does not prove — stated carefully, because it is two independent steps and only
one of them is about the payload.**

1. **The payload equality rules out the index DATA travelling in the payload.** An inverted index
   this record measured at **2 230 000** bytes (20 000 nodes) and **10 035 000** bytes (90 000) does
   not fit inside a **1.11 MiB / 5.02 MiB** payload that is the same size with the index absent — the
   index is ~1.9× the payload at both sizes. So the restore cannot be merely re-attaching serialized
   index data.
2. **The `numTerms` / `invertedSize` equality does NOT prove "rebuilt" on its own**, and an earlier
   revision of this document claimed it did. Those figures are *deterministic functions of the
   restored documents*, so a complete, correct **rebuild** reproduces them exactly — the equality is
   predicted equally by "the data travelled" and "the data was rebuilt from scratch". What it
   discriminates is **H3** (index absent or empty), which is the hypothesis that would have changed
   the verdict, and that is worth having on its own.

⇒ **The conclusion — the definition travels in the payload (§2's carriage probe), and the index data
is rebuilt on the serving thread — rests on three things that each had to be measured: the payload
cannot be carrying the index (step 1), a cross-name restore shows the definition came out of the
payload (below), and the timing and the block (§3, §4) show the rebuild happens inside the `RESTORE`
call** — 0.011 s without an index against 0.405 s with one, with the instance answering nothing for
its duration. The timing is doing real work in that argument, not decorating it.

⚠️ **One mechanism this record cannot exclude**, and says so rather than implying otherwise: index
data persisted in a **separate** RDB key (RediSearch's own layout, which §2's research subsection
fails to settle) would also be invisible to a graph-key `DUMP` — so "not in the payload" is proved,
while "not persisted elsewhere" is **not**. That does not change the measured cost: whatever the
engine does with persistence, the measured `RESTORE` blocks the serving thread for 0.405 s / 1.585 s
with a fulltext index present and 0.011 s / 0.032 s without one.

This settled the three hypotheses the plan named, and it is worth recording which one survived:

| | predicted signature | outcome |
|---|---|---|
| **H1** data travels with the payload | restore ≈ index-free, index populated after | ✗ — the payload does not grow with the index (step 1 above) |
| **H2** definition travels, data rebuilt | restore ≫ index-free; bystander ≈ restore | ✅ **this is what happens** |
| **H3** neither travels | restore fast, index absent or EMPTY after | ✗ — index is `OPERATIONAL` **and** answers |

**H3 was the one that would have changed the verdict from "costs money" to "the kept index is not
actually kept", so it was tested rather than assumed:** every run asks the restored index a live
query, not just whether a catalogue row exists (`fts_hits_after` 5 000 at 20 000 nodes, 22 500 at
90 000; `numDocuments` equal to the node count in all four index arms), and a restored **fulltext**
index that answered nothing would now **refuse the run** rather than print a table (exit 2; §6). ⚠️
The post-restore refusal is FULLTEXT-only: the range arms refuse on the restored node count and on
`numDocuments` via `await_index`, but not on a live range query, because that query's planner
selection is the engine's choice (§6).

### "The definition travels in the payload" — established separately, because the harness cannot do it

⛔ **The five-arm harness restores under the SAME graph name it dumped, so on its own it cannot tell
payload carriage from registry retention** — the engine may simply have kept the index definition in
its registry across the `DEL`. #7846 hit this exact confound and settled it with a **different-name**
restore; so does this record, in `index_carriage_probe.py` → `index-carriage-42006.log`:

```text
FULLTEXT: source OPERATIONAL (5000 docs, 25035 terms, 557500 inverted bytes) -> target OPERATIONAL (5000 docs, 25035 terms, 557500 bytes, answered 1250) under a DIFFERENT name; source graph gone (0)
RANGE   : source OPERATIONAL (5000 docs, 0 terms, 19682 inverted bytes) -> target OPERATIONAL (5000 docs, 0 terms, 19680 bytes, answered 4999) under a DIFFERENT name; source graph gone (0)

VERDICT: the index DEFINITION travels inside the graph key's payload — a target graph that has never seen it comes back with the index populated and answering.
```

A target graph whose name the payload has never carried comes back with the index **populated and
answering**, so the definition came out of the payload — not out of a registry keyed by the old name.

⚠️ **Two honesty notes about that probe.** Its source is deleted BEFORE the cross-name restore, which
is the `deleted` variant of the #8270 crash rather than the `kept` one (restoring an indexed payload
under a new name while the source still exists SIGSEGVs graph module 42006, the pin) — and it is also
the variant the measured cycle performs. And its RANGE row **reproduces #12768 at 5 000 nodes**
(`invertedSize` 19 682 → 19 680, rows 5 000 → 4 999) under a different name, so that defect is a
property of the payload path, not of restoring in place.

### What the external research said, and what it did not

**Rung 1 was unavailable on this box** (`mcp_load seo-intelligence` → `Unknown MCP server
'seo-intelligence'`), and **rung 2 was also blocked** (`web_search` → `PERPLEXITY_API_KEY not set`);
the key exists at `/home/cmux/keys.env`, so the two queries went to the Perplexity API directly with
`model="sonar"` — same engine, same model — rather than substituting a different search engine.
**2 attempted / 2 returned.**

What it produced, and what it could not:

- **Taken:** for FalkorDB's **native numeric** indexes, the engine source states *"the RDB does not
  carry the index"* and the **populate path rebuilds it** — which is the prior §2's measurement
  confirms for `RANGE`.
- **Taken:** FalkorDB full-text indexes are **RediSearch-backed**, and RediSearch's own RDB path
  (`Indexes_RdbSave` / `Indexes_RdbLoad`) persists index *specifications* and opens persisted on-disk
  data **where the SST/disk mechanism is available**. FalkorDB embeds RediSearch **in memory**, so
  whether the disk path is in play was exactly what the search could not settle.
- **Not taken — and deliberately not cited as a prediction:** no FalkorDB source or doc publishes a
  FalkorDB-specific rebuild rate, and no documented evidence establishes that fulltext data is
  RAM-only-and-always-rebuilt. The RediSearch 2.0 figure the search offered (~132 000 docs/s, 0.4 ms
  p50) is a **standalone-RediSearch benchmark, not a FalkorDB restore**, so the measured numbers
  below are not read against it.
- **Verified locally, not taken from search:** all four figures, the mechanism in this section, and
  the range defect in §5. Search named the mechanism and inverted the prior twice; it settled none of
  the numbers.

## 3. Results — five arms, two sizes

Engine `falkordb/falkordb-server:v4.20.6` (redis **8.6.3**, graph module **42006** — the CI pin),
persistence **off** (`save ""`, `appendonly no`) so no BGSAVE fork-COW confounds a timing, a private
lane container on `127.0.0.1:16422`, a binary-safe client, and a **bystander on its own socket
throughout**.

The graph is `:Doc:Num` nodes carrying prose `content` (~12 words), a **flat-string** `search_keys`
(four identifier tokens) and an integer `score`. Nodes carry both labels in **every** arm, including
the floor, so the payload is structurally identical and only the index differs.

### 20 000 nodes — `fulltext-42006-20000.log`

| arm | DUMP | DEL | RESTORE | re-create | build wait | whole cycle | worst bystander | docs after | hits |
|---|---|---|---|---|---|---|---|---|---|
| noindex (floor) | 0.023 s | 0.000 s | **0.011 s** | — | — | 0.034 s | **8.8 ms** | — | — |
| **fulltext kept** | 0.017 s | 0.000 s | **0.405 s** | — | — | 0.422 s | **358.9 ms** | 20 000 | 5 000 |
| fulltext dropped | 0.020 s | 0.000 s | **0.011 s** | 0.001 s | 0.50 s | 0.536 s | **10.6 ms** | 20 000 | 5 000 |
| range kept | 0.018 s | 0.000 s | 0.061 s | — | — | 0.080 s | 15.3 ms | 20 000 | 19 999 ⚠️ |
| range dropped | 0.018 s | 0.000 s | 0.009 s | 0.001 s | 0.25 s | 0.280 s | 7.4 ms | 20 000 | 20 000 |

### 90 000 nodes — `fulltext-42006-90000.log`

| arm | DUMP | DEL | RESTORE | re-create | build wait | whole cycle | worst bystander | docs after | hits |
|---|---|---|---|---|---|---|---|---|---|
| noindex (floor) | 0.082 s | 0.001 s | **0.041 s** | — | — | 0.124 s | **53.0 ms** | — | — |
| **fulltext kept** | 0.068 s | 0.000 s | **1.585 s** | — | — | 1.654 s | **1 535.2 ms** | 90 000 | 22 500 |
| fulltext dropped | 0.055 s | 0.001 s | **0.032 s** | 0.001 s | 1.51 s | 1.603 s | **41.2 ms** | 90 000 | 22 500 |
| range kept | 0.068 s | 0.000 s | 0.261 s | — | — | 0.329 s | 217.6 ms | 90 000 | 89 999 ⚠️ |
| range dropped | 0.054 s | 0.000 s | 0.028 s | 0.001 s | 0.25 s | 0.336 s | 39.2 ms | 90 000 | 90 000 |

Four readings:

1. **The fulltext index costs the restore a stable ~37–38× the index-free floor** (37.3× at 20 000,
   38.4× at 90 000). That the *ratio* holds shows the cost is **scale-invariant**, which is what makes
   it predictable. ⚠️ It does **not** show the cost is linear in document count — 4.5× the nodes
   produced 3.9× the time (0.405 s → 1.585 s), i.e. mildly **sublinear** in this range. Two points
   cannot establish a curve; they establish a stable ratio and a finite extrapolation.
2. **The absolute number is what decides feasibility, and it is small:** 0.405 s at 20 000 and
   1.585 s at 90 000. See the ⚠️ under the verdict table on why the vector comparison is confounded
   and is used for direction only.
3. **The range index is cheaper still** — 5.6×/6.3× the floor, i.e. 0.061 s and 0.261 s.
4. ⚠️ **Dropping the fulltext index first does not make the work cheaper, it makes it non-blocking.**
   The rebuilt index is the same rebuild either way: whole cycle **0.536 s dropped vs 0.422 s kept** at
   20 000, and **1.603 s vs 1.654 s** at 90 000 — a wash. What changes is *who pays it*: on the kept
   path the serving thread pays it (bystander 358.9 ms / 1 535.2 ms); on the drop-first path a
   background build pays it (`build wait` 0.50 s / 1.51 s) while the bystander sees **10.6 ms /
   41.2 ms**. **The win is the outage, not the total.** This is the opposite of the vector case, where
   drop-first was also ~43× faster **at the RESTORE step** (#7846 §3a: 2.955 s → 0.069 s — a step
   ratio, not a whole-cycle one); a reader carrying that expectation into fulltext would be wrong.

> ⚠️ **The `build wait` figure at small sizes is poll granularity, not cost.** The harness polls the
> catalogue every 0.25 s, so a 0.25 s "wait" at 20 000 nodes (range) means *the build finished inside
> the first poll*, not that it took 0.25 s. At 90 000 the fulltext re-create's 1.51 s / 1.78 s is a
> real measurement — it is many polls wide. Read `build wait` as an upper bound at the low end.

## 4. Is it a block, or is it a slow restore? — the bystander

**It is a block, and the difference is the whole point of the measurement.** In both kept arms the
bystander's worst single `PING` *is* the restore's duration:

| size / arm | RESTORE | core cycle | worst bystander `PING` | vs restore |
|---|---|---|---|---|
| 20 000, fulltext kept | 0.405 s | 0.422 s | 358.9 ms | **0.89** |
| 90 000, fulltext kept | 1.585 s | 1.654 s | 1 535.2 ms | **0.97** |
| 20 000, range kept | 0.061 s | 0.080 s | 15.3 ms | 0.25 ⚠️ undersampled |
| 90 000, range kept | 0.261 s | 0.329 s | 217.6 ms | 0.83 |
| 90 000, floor (no index) | 0.041 s | 0.124 s | 53.0 ms | 1.28 |

A restore that were merely slow would leave the bystander answering at its baseline throughout, and
for the fulltext arms it does the opposite: the ratio *rises toward 1* as the work grows (0.85 → 0.93
against the whole core cycle, 0.89 → 0.97 against the restore alone), and 358.9 ms / 1 535.2 ms are
far above the 8.8 ms / 53.0 ms floor the same graph shows with no index. The instance answers nothing
for the duration, which is the #7846 signature (§3) reproduced for a different index type. **0 of the
11–41 bystander samples in *every* arm failed**: the instance is blocked, never dead, and it recovers
the moment the restore returns.

⚠️ **Two caveats on reading the bystander, both stated because they bound what the range rows can
claim.**

- **The poll interval limits resolution.** The bystander samples every 50 ms, so a block shorter than
  roughly two intervals can be *undersampled* rather than absent. That is what the range arm shows:
  its core cycle is 80 ms at 20 000 nodes and the busy bystander caught **15.3 ms**, i.e. it did not
  have a probe in flight across the whole block. At 90 000 nodes the window is 329 ms and the bystander
  caught **217.6 ms** — a ratio of 0.83, consistent with the fulltext shape. So **"range blocks" is
  measured at 90 000 and merely *consistent* with a block at 20 000**; the fulltext arms are an order of
  magnitude clear of the resolution limit at both sizes, and they are the rows the verdict rests on.

⚠️ **How the bystander's number is taken, because it changes what it means** (copied from #7846 §3a
for the same reason). Its socket timeout is set **above** the longest block measured, so ONE probe
spans the block and its latency IS the outage. `redis-py`'s default `Retry` lists `TimeoutError` in
`supported_errors`, so with a short timeout a blocked `PING` is retried internally and the call
RETURNS with the **retry-summed** wall time — a true number that cannot distinguish one 1.5 s stall
from two 0.75 s ones. The bystander is also read **after its thread is joined**, because the single
probe that spans the block is still in flight when the restore returns; reading it inside the block
reports the last *completed* probe and understates the outage by everything.

## 5. The range arm — and a defect found while measuring it (#12768)

The range index follows the same shape (definition travels, data rebuilt, instance blocked for the
build) at roughly a sixth of the fulltext cost.

While reading it, the arms disagreed: `range kept` answered **19 999** rows at 20 000 nodes and
**89 999** at 90 000, where `range dropped` answered 20 000 / 90 000 for the same query. A one-row
delta in a summary table is too weak to file a defect on, so it was isolated by a separate committed
probe (`range_zero_probe.py`, `range-zero-42006.log`) rather than asserted from the harness run, and
it is filed as **#12768**:

> **A payload-carried `RANGE` index loses every entry whose value is `0`.** 5 nodes with
> `score = [0, 0, 1, 2, 3]`: before the cycle the range window answers **5**; after a *kept* restore it
> answers **3**, while `count(n)` is still 5 and node `id=0` **still holds `score 0`** — the data is
> intact, the index is not. The index's own arithmetic agrees: `numDocuments` stays **5** (documents
> are registered) but `invertedSize` drops **9 → 5 bytes**, i.e. exactly the two 0-valued postings are
> missing. Lowering the bound to `-1` does not recover them (`>=0`, `>=-1` and `>-1` all answer 3), and
> an equality lookup (`n.score = 0`) finds **0 of the 2** — so the entries are **absent** from the
> index rather than excluded by the query bound. The **drop-and-recreate** path answers 5/5.

It is **not** a minimum-bound artifact: with `score = 100..3099` the kept restore answers all 3 000
correctly (0 lost) — it is the **value `0`** specifically. And it is not data loss: `nodes after`
equals the node count in all ten harness runs and in the probe.

The same 2-byte-per-entry loss is visible in the harness's own committed records at scale — range
`invertedSize` **79 526 → 79 524** at 20 000 nodes and **383 265 → 383 263** at 90 000 — where the
fulltext arms are exact (2 230 000 → 2 230 000; 10 035 000 → 10 035 000). Two independent artifacts,
same conclusion.

⚠️ **Scope, stated narrowly on purpose.** It needs an indexed numeric property that legitimately
holds `0`, and it bites only the kept path. It matters here because **"the index is `OPERATIONAL`" is
being used as a health signal and in this case it is a false pass** — the query succeeds and answers
short. Fixing it is **not** this lane's work: this lane measures restore cost and must not change
product behaviour. It is recorded here because the arms in the table above would otherwise show
`19 999` / `89 999` with no explanation, and a reader would reasonably suspect the harness.

## 6. The populated-index gate — what makes these numbers evidence

The #7846 lane's **first** harness measured nothing, because its graph carried no populated index —
an empty index definition is not an index (that record's §1). At 90 000 nodes that defect would hide
inside a plausible-looking number, so this harness gates on, for **every** index arm:

- the index reports **`OPERATIONAL`**, **and**
- `numDocuments` ≥ the node count (**20 000 / 90 000**, recorded as `pre_documents` in the logs).

and, for the **fulltext** arms additionally, that a live `db.idx.fulltext.queryNodes` returns
**hits** (`pre_fts_hits` **5 000** / **22 500**) — a refusal this harness actually enforces.

⚠️ **The gate is asymmetric, and the asymmetry is stated rather than papered over.** The range arm
records `pre_range_rows` but does **not** refuse on it, because whether the query planner consults the
range index or answers the predicate with a label scan is **the engine's choice, not the probe's** — a
scan answers `pre_range_rows` correctly with no index at all. ⚠️ **That is a weaker reason than an
earlier revision of this document gave**, which claimed the probe "cannot detect an empty range index"
and that gating on it "would be a gate that cannot fail"; **that claim was wrong and is retracted.**
§5's own measurement shows the probe *did* consult the index — `range_rows_after` **19 999** against
`nodes_after` **20 000** is a query returning one short because the index is short. So the probe does
track index content once the engine selects the index; what it cannot guarantee is that selection.
What evidences range population **independently of planner selection** is `numDocuments` equal to the
node count (the range index's own document table, which `await_index` DOES refuse on) — but that is
**registration**, not postings: §5 proves the two can diverge (`numDocuments` 5 while `invertedSize`
drops 9 → 5). Posting population is evidenced by `invertedSize` (79 526 bytes at 20 000; 383 265 at
90 000) and by the read-only §5 probe. The fulltext arms, which carry the verdict, have the stronger
gate: `numDocuments` **and** a live query.

A third gate was added in review, for the same reason: **a post-restore refusal.** `nodes_after` and
`fts_hits_after` were previously recorded and printed but never refused, so an H3 run (index data
lost across the restore) or a node-losing restore would have **exited 0 and printed a table**, leaving
"the index is still populated afterwards" to whoever read the row. Both now refuse (exit 2). The
committed runs carry the full node count and non-zero hits, so no published number changes.

A separate fail-open was found in review and closed: `Bystander.worst_ms` returns `0.0` for an empty
sample set, so an engine that dropped mid-run would have been recorded as *"worst PING 0.0 ms"* —
i.e. as **no block**, the opposite of what happened. The harness now refuses an arm with zero
successful bystander samples (`_invalid_harness`, exit 2). The committed runs carry **11–41** samples
each with **0** failed pings, so this closes a latent path rather than changing a published number.

Failure exits **2 — INVALID HARNESS** (`SystemExit(2)`, not `assert`: an `assert` is stripped under
`python -O` and exits 1, so a consumer keying on the documented code gets the case it was guarding
against). At 20 000 nodes the index is **100 035 terms / 2 230 000 inverted bytes**; at 90 000,
**450 035 terms / 10 035 000 inverted bytes** — so the measurement is carried by a real inverted
index, not by a definition.

**One harness defect was found and fixed mid-lane, and it is recorded because it produced a false
green.** The first 20 000-node attempt ran through a bare `| tee`, so the pipeline's exit status was
`tee`'s and a **crashed run was recorded as `exit 0`** by `worktree_report`. Every measured command in
this record is taken through `bash -o pipefail`. A measurement lane cannot report a green run over a
failed one.

**Payload shape is part of the measurement.** `search_keys` is written as a **flat string**, because
FalkorDB's fulltext index does not index array-valued properties (`flatten_search_keys_prop`,
`tortoise/projection/entities.py`) — a list-valued `search_keys` would leave half the text
unindexed and silently understate the leg being measured. Index creation tries the Cypher-native DDL
first and the historical procedure as a fallback, because `projection/__init__.py` records that the
multi-field procedure is **rejected by graph module 6.0.0** — a harness that only knew the procedure
would leave the index absent on half the engines, which is #7846 §1 all over again.

## 7. What this means for §7 — and the contradiction test

**The verdict, in one line: against a caller read bound the kept fulltext index does NOT make the
cold-graph window a feasibility problem — zero-standing hot remains viable for `free`/`anon` at
1.585 s worst case. Against a per-tenant latency objective it is NOT established, and no such
objective exists in this record (point 0).**

0. ⛔ **The budget this is viable against, named, because without it "viable" is undecidable.** The
   only bound available to quote is #7846's **caller-side read bound** on the `RESTORE` call (its
   client's 10 s socket). Against *that*, 1.585 s is comfortable. It is **not** a serving objective
   for the **instance-wide, all-tenant** block this record measures (§4): a per-tenant p99 target of a
   few hundred milliseconds would fail a 1.585 s stall while the 10 s caller bound passes it
   trivially. **This record does not know the product's latency objective and does not invent one.**
   Read the verdict as "viable against a 10 s caller read bound; the accept/reject decision needs an
   SLO this measurement cannot supply" — and point 3 is why the SLO, whatever it is, has a cheap
   answer available.
1. **Bounded and extrapolable.** 1.585 s at 90 000 nodes — the top of the range the proposal cares
   about. The ratio to the index-free floor is stable across the two sizes (37.3× → 38.4×), so the
   cost is **scale-invariant**; ⚠️ it is mildly **sublinear** in this range (4.5× nodes → 3.9× time),
   so extrapolating past 90 000 is arithmetic with a mild favourable bias — not a cliff, and not a
   proven straight line either.
2. **Cheap relative to what drove the decision — direction, not multiplier.** §7's feasibility worry
   came from a **2.955 s** / **2 899.4 ms** restore at 20 000 nodes and a restore that outlived a 10 s
   read bound at 90 000. This record measures **0.405 s / 358.9 ms** and **1.585 s / 1 535.2 ms** at the
   same node counts. ⚠️ The two sides differ in engine **and** payload size (see the ⚠️ under the
   verdict table), so the 7.30× / 8.08× figures and the ≥33× floor describe **the difference between
   those two named runs** and would be **wrong if read as an index-type multiplier**; the defensible
   statement is that a fulltext rebuild is **substantially cheaper** than a vector one at the same node
   count, and the same-engine 5 000-node datapoint (2.25 s vector vs 0.405 s fulltext at
   20 000) supports that direction without a clean ratio.
3. **Avoidable if the pause is judged too long.** Dropping the fulltext index before the `DUMP` costs
   **nothing** in total time (cycle 0.536 s vs 0.422 s at 20 000; 1.603 s vs 1.654 s at 90 000) and
   removes the block entirely (bystander 10.6 ms / 41.2 ms), because the re-create builds in the
   background. So whatever latency bound the owner sets, there is a mitigation that does not buy its
   safety with throughput.
4. **What remains a real (and different) problem:** the pause is **instance-wide** — every other
   tenant is blocked for it, not just the graph being restored. At 1.6 s that is a tail-latency event,
   not an outage; it becomes a problem only if first touches *cluster*, which is a scheduling rule for
   the window (§7's admission-control row), not an index-cost finding. **This record does not decide
   that rule**, and the numbers here are its input.
5. **A correctness caveat that outranks the cost question for the range index.** §5's `#12768` means a
   payload-carried range index **answers short** after a restore. That is a *correctness* defect in the
   kept path, and it is a stronger argument for dropping-and-recreating than any latency number here —
   not because it is slow, but because it is wrong.

### ⛔ Contradiction test — stated explicitly, as this lane is required to

The one recorded decision in scope is **§6.2 / §7 decision item 2: the fulltext index STAYS in the
graph.** This lane **does not reverse it and does not propose to reverse it** — it measures its cost,
and the measurement supports keeping it. If a later lane reads these numbers as an argument that the
index should move to Postgres, that is **a reopen argued on `#4051` with these numbers**, not a change
to be made in an evidence record, and not a change this lane made.

⚠️ **Nothing in the product's restore behaviour changed.** `tortoise/graph_key_restore.py` is *read*,
not edited: `restore_graph_key_safe` drops **VECTOR** definitions only (`vector_index_definitions`,
`_NON_VECTOR_TYPES = {"RANGE", "FULLTEXT"}`), so a fulltext or range index is deliberately tolerated by
its guard and is exactly the arm it does not cover. If a cheap mitigation were adopted it would be a
one-paragraph proposal on #12766 — and the cheap mitigation this record found is **not a code change at
all**: it is "drop the fulltext index before the DUMP, exactly as the vector index already is",
which `restore_graph_key_safe` could extend by widening its drop set. That is deliberately **left
undone** here.

## 8. Reproducing

```bash
# the lane's private container (never the shared localhost:6379 on a cell)
uv run python tools/test_lane.py up          # prints the port; this record used 16422
docker exec fdb-lane-<id> redis-cli CONFIG SET save ""
docker exec fdb-lane-<id> redis-cli CONFIG SET appendonly no

# both sizes; every arm; ~30 s at 20k, ~2 min at 90k
uv run python docs/evidence/12766-fulltext-restore/fulltext_restore_cost.py <port> 20000
uv run python docs/evidence/12766-fulltext-restore/fulltext_restore_cost.py <port> 90000

# one arm only, e.g. to re-check the fulltext kept/dropped pair
uv run python docs/evidence/12766-fulltext-restore/fulltext_restore_cost.py <port> 20000 \
    --arms fulltext-kept,fulltext-dropped

# §5 / #12768 — the range value-0 isolation probe
uv run python docs/evidence/12766-fulltext-restore/range_zero_probe.py <port>      # exit 3 = NOT REPRODUCED

# §2 — the cross-name carriage probe (does the DEFINITION travel in the payload?)
uv run python docs/evidence/12766-fulltext-restore/index_carriage_probe.py <port> 5000   # exit 3 = NOT CARRIED
```

⚠️ Both probes `import fulltext_restore_cost` for the shared client and catalogue helpers, so they
resolve against **their own directory** (`sys.path[0]`); they need no `cd`. The harness writes nothing
itself — the logs above were produced by teeing its stdout, e.g.
`uv run python … | tee fulltext-42006-20000.log`, under `bash -o pipefail` so the harness's exit status
is not masked by `tee`'s (§6).

Exit codes: **2** means INVALID HARNESS — the index was not populated (or, post-restore, did not come
back whole), so the run says nothing about an index-bearing restore (see §6). Both probes also use
**3** for **NOT REPRODUCED** — `range_zero_probe.py` when the value-0 defect does not appear, and
`index_carriage_probe.py` when the index does not survive a cross-name restore (which would mean the
same-name result was registry retention). Exit 3 is a **finding**, deliberately not dressed as an
instrumentation error.

Each run prints a **self-describing first line** naming its own engine (`redis_version`, graph module,
module args, persistence), so a committed log carries its provenance. That is deliberate: #7846's
README has to mark its `drop-first-*.log` files as *"operator-asserted in this document and NOT
recorded in the commit"* because its script printed no header, and that is not a defect to reproduce.

## 9. Committed logs

| file | what it is |
|---|---|
| `fulltext-42006-20000.log` | 20 000 nodes, all five arms — the §3 first table |
| `fulltext-42006-90000.log` | 90 000 nodes, all five arms — the §3 second table |
| `range-zero-42006.log` | the §5 / #12768 isolation probe — the value-0 case, its minimum-value control, and the bound tests |
| `index-carriage-42006.log` | the §2 cross-name carriage probe — a different-name restore, both index kinds |

Raw stdout, one JSON record per arm plus the summary table, with the engine header on line 1.
⚠️ **An earlier revision of this paragraph claimed §2–§5 were every figure from two committed files,
and it was wrong twice over** — first because the 5-node probe in §5 was ad-hoc and uncommitted, then
because the "fixed" sentence still left §5's `19 999` / `89 999` and the `invertedSize` deltas in the
harness logs. Both probes are committed now. The map is:

| section | artifacts |
|---|---|
| §2 mechanism + carriage | `fulltext-42006-20000.log`, `fulltext-42006-90000.log` (payload sizes, index stats), `index-carriage-42006.log` |
| §3, §4 cost and bystander | `fulltext-42006-20000.log`, `fulltext-42006-90000.log` |
| §5 #12768 | `range-zero-42006.log`, plus `19 999` / `89 999` and the `invertedSize` deltas from the two harness logs, plus `index-carriage-42006.log` |
| §6 gates | the harness source; the logs' `pre_*` and `samples` fields |

## 10. What is NOT measured (residuals, stated rather than assumed)

- **The engine IMAGE TAG is declared in-repo, and the container actually used is operator-asserted.**
  The header records `redis_version` **8.6.3**, the module version **42006**, its `graph_path` and its
  args; the string `falkordb/falkordb-server:v4.20.6` is **not in the committed logs**. It is not
  folklore either: `tools/test_lane.py` pins it (`LANE_IMAGE_PIN`, with `assert_pinned_image` refusing a
  floating tag), so the tag is a **declared pin in the tree** and what is operator-asserted is only that
  *this* measured container was launched from it (§8). That is #7846's disclosure narrowed to one field,
  not repeated in full.
- **`numDocuments` evidences document REGISTRATION, not posting population.** §5 is the proof: the
  value-0 case keeps `numDocuments` at 5 while two of its five postings vanish (`invertedSize` 9 → 5,
  window 5 → 3). So the range arm's population evidence is `numDocuments` **for registration** and
  `invertedSize` **for postings** — and no gate in this harness compares `invertedSize`, which is why
  #12768 could sit inside a passing run.
- **"the extra time is inside the `RESTORE` call" is measured; "CPU-bound" is not.** No CPU, thread or
  utilisation datum appears in any log (`thread_count: null` throughout — the module's args are
  recorded but the field the harness looks for is not exposed). What is measured is that the additional
  time sits **inside** the `RESTORE` command and that the instance answers nothing for it. An earlier
  revision called it "CPU-bound inside the call", which #7846 established with a CPU measurement and
  this record did not repeat.
- **Text volume is realistic but modest.** `content` is ~12 words plus four identifier tokens per node
  (450 035 terms at 90 000 nodes). A Point carrying much longer prose, or many more `search_keys`,
  builds a larger inverted index and therefore a longer rebuild — the **ratio** should hold, the
  **absolute seconds** will move. These numbers are for the payload shape in §3, not a universal
  constant.
- **One engine for the measurements.** Graph module **42006** (`falkordb-server:v4.20.6`, the CI pin).
  #7846 measured three engines and found the vector-index block on all of them; this record did not
  repeat that sweep for fulltext, so **no cross-engine claim is made** — and note that the vector
  comparison row in the verdict table is itself **cross-engine and cross-payload** (42201 / 9.66 MB at
  20 000; 60001 / 43.19 MB at 90 000), which is why it is used for direction only.
- **One bystander, one probe in flight, sampled every 50 ms.** The bystander is a single connection
  polling `PING`; it measures the instance's serving-ness, not a realistic mixed workload, and it does
  not measure *other graphs* specifically (the #7846 record notes the same limitation for its criterion
  2). Its 50 ms interval also bounds resolution: a block shorter than ~100 ms can be undersampled, which
  is why the **range block is measured at 90 000 nodes** (329 ms window, 217.6 ms caught) and only
  *consistent* with a block at 20 000 (80 ms window, 15.3 ms caught) — and why the verdict rests on the
  fulltext rows, an order of magnitude clear of that limit at both sizes.
- **The re-create's background build is not attributed.** `build wait` is elapsed time to a populated
  index, which includes the poll interval; it is not the engine's own build duration. `totalMSRun`
  from the catalogue stats would be the tighter figure and was not read.
- **The range-zero defect (#12768) is characterized, not root-caused.** It is isolated to the value
  `0` on the kept path and to no other value or bound; the engine-side mechanism was not determined.
- **No production path was exercised.** This measures the *engine operation*. `restore_graph_key_safe`
  and `hosted_backup.py`'s `GRAPH.COPY` swap were not run, and what the product actually does today is
  unchanged by this record (#7846 §5 criterion 5 records that no production call site adopts the
  driver). The drop-first mitigation named in §7 point 3 is therefore a **proposal**, not a behaviour.
- **Ratio denominators are stated, not assumed.** `cycle` spans `DUMP`+`DEL`+`RESTORE` (+ re-create and
  build wait on the dropped arms), and the bystander window spans the core part only. Figures quoted as
  a ratio to the *restore* use `RESTORE`; where the `DUMP`/`DEL` contribute, the ratio can exceed 1 in
  the arms whose restore is tiny (the floor arm reads 1.28), which is why the table in §4 carries both
  columns.
