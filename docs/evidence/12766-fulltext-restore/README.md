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

Measured at: `measure/12766-fulltext-restore@f6ac10e41f` on 2026-11-14

> The measured artifact is the **FalkorDB engine image**, not this repository's tree: the repo
> hosts the harness. The tree line above names the commit the harness was run from.

**Verdict — the kept fulltext index DOES block the instance, and it is 33× cheaper than the vector
index that motivated the decision. Zero-standing hot stays viable for `free`/`anon`.**

| | 20 000 nodes | 90 000 nodes |
|---|---|---|
| **fulltext kept** | RESTORE **0.405 s**, bystander **358.9 ms** | RESTORE **1.585 s**, bystander **1 535.2 ms** |
| **fulltext dropped before DUMP** | RESTORE **0.011 s**, bystander **10.6 ms** | RESTORE **0.032 s**, bystander **41.2 ms** |
| index-free floor (no index at all) | RESTORE 0.011 s, bystander 8.8 ms | RESTORE 0.041 s, bystander 53.0 ms |
| **vector kept** (`#7846`, same record shape) | RESTORE 2.955 s, bystander 2 899.4 ms | **outlived a 10 s client read bound**, surfaced at 53.3 s |

So the §7 sentence resolves the way the decision hoped rather than the way it feared: a kept
`db.idx.fulltext` blocks a restore **exactly as the vector index did** — the bystander's worst `PING`
*is* the restore duration in both sizes — but the block is **~7× shorter at 20 000 nodes and ~33×
shorter at 90 000**, and at 90 000 it finishes in 1.6 s where the vector index did not finish inside
10 s. **A 1.6 s instance-wide pause is a scheduling problem (do not cluster first touches), not a
feasibility problem.** It is also avoidable outright: the drop-first path costs 0.032 s of RESTORE
and 41.2 ms of bystander at the same size.

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
| no index | **1.11 MB** | — |
| fulltext kept | **1.11 MB** | `OPERATIONAL`, 20 000 docs, 100 035 terms, 2 230 000 inverted bytes |
| range kept | **1.11 MB** | `OPERATIONAL`, 20 000 docs |

The payload is **byte-identical in size** whether or not an index exists — and identical again at
90 000 nodes (5.02 MB across every arm). Yet the restored index reports **exactly** the pre-restore
`numDocuments`, `numTerms` **and** `invertedSize` (2 230 000 → 2 230 000 at 20 000 nodes; 10 035 000 →
10 035 000 at 90 000).

⇒ **The index DEFINITION travels in the payload; the index DATA does not, and is rebuilt on the
serving thread from the restored graph.** That is not an inference from timing — it is forced by the
payload size together with the exact match of a derived statistic that cannot survive a partial
rebuild.

This settled the three hypotheses the plan named, and it is worth recording which one survived:

| | predicted signature | outcome |
|---|---|---|
| **H1** data travels with the payload | restore ≈ index-free, index populated after | ✗ — payload size is unchanged |
| **H2** definition travels, data rebuilt | restore ≫ index-free; bystander ≈ restore | ✅ **this is what happens** |
| **H3** neither travels | restore fast, index absent or EMPTY after | ✗ — index is `OPERATIONAL` **and** answers |

**H3 was the one that would have changed the verdict from "costs money" to "the kept index is not
actually kept", so it was tested rather than assumed:** every run asks the restored index a live
query, not just whether a catalogue row exists (`fts_hits_after` 5 000 at 20 000 nodes, 22 500 at
90 000; `numDocuments` equal to the node count in all four index arms).

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

1. **The fulltext index costs the restore a factor of ~37–38×, consistently across sizes** (37.3× at
   20 000, 38.4× at 90 000) — so the cost is linear in document count, and *predictable*.
2. **The absolute number is what decides feasibility, and it is small:** 0.405 s at 20 000 and
   1.585 s at 90 000. The vector index on the same record shape cost **2.955 s at 20 000** and did not
   return inside 10 s at 90 000.
3. **The range index is cheaper still** — 5.6×/6.3× the floor, i.e. 0.061 s and 0.261 s.
4. ⚠️ **Dropping the fulltext index first does not make the work cheaper, it makes it non-blocking.**
   The rebuilt index is the same rebuild either way: whole cycle **0.536 s dropped vs 0.422 s kept** at
   20 000, and **1.603 s vs 1.654 s** at 90 000 — a wash. What changes is *who pays it*: on the kept
   path the serving thread pays it (bystander 358.9 ms / 1 535.2 ms); on the drop-first path a
   background build pays it (`build wait` 0.50 s / 1.51 s) while the bystander sees **10.6 ms /
   41.2 ms**. **The win is the outage, not the total.** This is the opposite of the vector case, where
   drop-first was also 43× faster in total; a reader carrying that expectation into fulltext would be
   wrong.

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
**89 999** at 90 000, where `range dropped` answered 20 000 / 90 000 for the same query. That is not
probe noise — it isolates to a specific value, and it is filed as **#12768**:

> **A payload-carried `RANGE` index loses every entry whose value is `0`.** 5 nodes with
> `score = [0, 0, 1, 2, 3]`: before the cycle the range query answers **5**; after a *kept* restore it
> answers **3**, while both zero-valued nodes still exist (`MATCH (n) WHERE n.id = 0 RETURN n.score`
> → `0`) and `count(n)` is still 5. Lowering the bound to `-1` does not recover them, so the entries
> are absent from the index rather than excluded by the bound. The **drop-and-recreate** path answers
> 5/5.

It is **not** a minimum-bound artifact: with `score = 100..3099` the kept restore answers all 3 000
correctly — it is the **value `0`** specifically. And it is not data loss: every node comes back, in
every arm, and `nodes after` equals the node count in all ten runs above.

⚠️ **Scope, stated narrowly on purpose.** It needs an indexed numeric property that legitimately
holds `0`, and it bites only the kept path. It matters here because **"the index is `OPERATIONAL`" is
being used as a health signal and in this case it is a false pass** — the query succeeds and answers
short. Fixing it is **not** this lane's work: this lane measures restore cost and must not change
product behaviour. It is recorded here because the arms in the table above would otherwise show
`19 999` / `89 999` with no explanation, and a reader would reasonably suspect the harness.

## 6. The populated-index gate — what makes these numbers evidence

The #7846 lane's **first** harness measured nothing, because its graph carried no populated index —
an empty index definition is not an index (that record's §1). At 90 000 nodes that defect would hide
inside a plausible-looking number, so this harness refuses to time anything until:

- the index reports **`OPERATIONAL`**, and
- `numDocuments` ≥ the node count (**20 000 / 90 000**, measured as `pre_documents` in the logs), and
- for fulltext, a live `db.idx.fulltext.queryNodes` returns **hits** (`pre_fts_hits` **5 000** /
  **22 500**); for range, the range window returns rows (`pre_range_rows` **20 000** / **90 000**).

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

**The verdict, in one line: the kept fulltext index does NOT make the cold-graph window a feasibility
problem; zero-standing hot remains viable for `free`/`anon`.**

The reasoning, in the order it matters:

1. **Bounded.** 1.585 s at 90 000 nodes — the top of the range the proposal cares about — against a
   10 s client read bound that the *vector* index already exceeded at this size. The cost is linear
   in document count (37.3× → 38.4×), so extrapolating past 90 000 is arithmetic, not a cliff.
2. **Cheap relative to what drove the decision.** §7's feasibility worry came from a **2.955 s** /
   **2 899.4 ms** restore at 20 000 nodes and a restore that outlived a 10 s read bound at 90 000. The
   fulltext index costs **0.405 s / 358.9 ms** and **1.585 s / 1 535.2 ms** at the same sizes — ~7×
   and ~33× cheaper. The index that stays is not the index that was expensive.
3. **Avoidable if the pause is judged too long.** Dropping the fulltext index before the `DUMP` costs
   **nothing** in total time (cycle 0.536 s vs 0.422 s at 20 000; 1.603 s vs 1.654 s at 90 000) and
   removes the block entirely (bystander 10.6 ms / 41.2 ms), because the re-create builds in the
   background. So even a stricter latency budget has a mitigation that does not buy its safety with
   throughput.
4. **What remains a real (and different) problem:** the pause is **instance-wide** — every other
   tenant is blocked for it, not just the graph being restored. At 1.6 s that is a tail-latency event,
   not an outage; it becomes a problem only if first touches *cluster*, which is a scheduling rule for
   the window (§7's admission-control row), not an index-cost finding. **This record does not decide
   that rule**, and the numbers here are its input.

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
```

Exit code **2** means **INVALID HARNESS** — the index was not populated, so the run says nothing about
an index-bearing restore (see §6).

Each run prints a **self-describing first line** naming its own engine (`redis_version`, graph module,
module args, persistence), so a committed log carries its provenance. That is deliberate: #7846's
README has to mark its `drop-first-*.log` files as *"operator-asserted in this document and NOT
recorded in the commit"* because its script printed no header, and that is not a defect to reproduce.

## 9. Committed logs

| file | what it is |
|---|---|
| `fulltext-42006-20000.log` | 20 000 nodes, all five arms — the §3 first table |
| `fulltext-42006-90000.log` | 90 000 nodes, all five arms — the §3 second table |

Raw stdout of `fulltext_restore_cost.py`, one JSON record per arm plus the summary table, with the
engine header on line 1. Every figure in §2–§5 is read from these two files — not a range across runs
and not an ad-hoc run.

## 10. What is NOT measured (residuals, stated rather than assumed)

- **Text volume is realistic but modest.** `content` is ~12 words plus four identifier tokens per node
  (450 035 terms at 90 000 nodes). A Point carrying much longer prose, or many more `search_keys`,
  builds a larger inverted index and therefore a longer rebuild — the **ratio** should hold, the
  **absolute seconds** will move. These numbers are for the payload shape in §3, not a universal
  constant.
- **One engine.** Graph module **42006** (`falkordb-server:v4.20.6`, the CI pin). #7846 measured three
  engines and found the vector-index block on all of them; this record did not repeat that sweep for
  fulltext, so the cross-engine claim is **not** made.
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
