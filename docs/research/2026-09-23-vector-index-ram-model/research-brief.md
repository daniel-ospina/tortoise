---
title: Research brief — is the pgvector HNSW index RAM-mandatory?
type: engineering
domain: platform
doc_status: draft
created: 2026-09-23
subjects.team: epistemic-team
issue: "#4333"
aboutSubjects: Tortoise memory graph
aboutObjects: STORAGE-ARCHITECTURE.md, pgvector, HNSW, resident index
---

# Research brief — is the pgvector HNSW index RAM-mandatory?

**Date:** 2026-09-23 · **Question owner:** storage architecture (`docs/architecture/STORAGE-ARCHITECTURE.md`)
**Resolves:** review finding **A1** / decision question **D1** (`REVIEW-CONSOLIDATED-2026-09-23.md`)
**Domain classification:** **Complicated × Complex** — the mechanism is well-understood and documented (Complicated); the
cost/architecture consequence interacts with tenancy, partitioning and our access pattern (Complex). Depth: **Deep**.
**Scope flags:** `--domain=engineering`

---

## 0. Problem reframing (Step 0)

**As asked:** *"Is the pgvector HNSW index RAM-mandatory, or disk-backed with a latency cliff?"*

**5 Whys:** Why does the RAM question matter? → it decides the cost model. → Why does the cost model matter? → because
at 1,000 tenants a RAM-priced index is unprovisionable. → Why would it be RAM-priced? → because §12.1 says HNSW "does not
follow the disk rule". → Why would §12.1 say that? → because a *performance* recommendation ("keep the index resident")
was read as a *structural* law ("the index IS resident"). **The root is a category error, not a fact gap.**

**Reframed:** *"The architecture needs to size Postgres for vector search; it is currently modelling a performance
recommendation as a hard memory requirement, and that mistake sets the price."* — the load-bearing word in the original
question is **"mandatory"**, and it is about **correctness/feasibility**, not speed. These are separable and the document
conflates them.

**Alternative framings (How Might We):**
- HMW make per-tenant cost independent of total vector count? → partition (index scope = tenant scope)
- HMW keep the index out of RAM entirely? → DiskANN / S3 Vectors (availability-constrained on Supabase)

**Assumptions mapped:**
| # | Assumption | Tag |
|---|---|---|
| A1 | "The HNSW index is loaded into RAM" (mechanism) | **REFUTED** (see §1) |
| A2 | "Every vector is a tax on every query forever" | **partly true** (index is read on every *unfiltered* ANN query; NOT on every query, and not resident) |
| A3 | "2.5M nodes ⇒ 7.1 GB ⇒ ×1,000 tenants = 7 TB of RAM" | **ARITHMETIC SOUND, MODEL WRONG** — 7 TB is a **disk** number (and only under a per-tenant reading of 2.5M); pricing it as RAM is the error (see §2.3). *(The doc's 7.1 GB rests on a different basis — it applies the rejected FalkorDB rate, ~2.85 KB/vec, to the **node** count rather than the **embedding** count; corrected to 2.05 KB/vec × 3.358M embeddings it is 6.9 GB. The two are not a pure rate conversion.)* |
| A4 | "The p90 96 ms figure is pgvector's spill penalty" | **MISCITED** — it is OpenSearch's designed disk mode |
| A5 | "disk rule does not apply to vectors" | **FALSE** — it applies; the *cliff* is what is different |
| A6 | "pgvector index sizing can be taken from our FalkorDB measurement" | **REFUTED** — different engine, different layout |

**Reverse the problem:** what if the index is genuinely disk-backed? Then the *opposite* conclusion follows — total
vector count is a **disk** cost, tenancy partitioning bounds the hot set, and §5's cost table was never invalidated.
That is what the evidence shows.

> **⚠️ Self-correction (verifier cycle 1, P1).** An earlier draft of §2.3 asserted the 7 TB was obtained by
> "charging every tenant for every other tenant's vectors", and that Reading 2 left RAM at 6.9 GB. **That was wrong
> and is corrected below.** The multiplication is arithmetically sound under the per-tenant reading; it is the
> *classification of the result as RAM* that is wrong. **The verdict does not change — but the reason does**, and a
> wrong reason is worse than no reason.

---

## 1. The verdict — Position B is correct; Position A is a misreading of a performance hint

**Claim 1 [HIGH].** The pgvector HNSW index is an **ordinary Postgres index**: heap-file pages in `shared_buffers`/OS
page cache, read on demand. It is **not** required to fit in memory, and a query whose index does not fit returns
**correct (same-recall) results** — slower, not different, not an error.

⚠️ **single-source on the sharpest leg:** the finding that Position A's *own* cited source says the opposite of what
it is cited for rests on **one** secondary article (The Next Platform, 2026-09-22). The article text is unambiguous,
but **verify when a second independent reading of pgvector's disk behaviour is available.**

Evidence (3 independent categories):
- **Official (pgvector README, FAQ, verbatim):** *"Do indexes need to fit into memory? **No**, but like other index
  types, you'll likely see better performance if they do."* — the same README's `maintenance_work_mem` note is about
  **build** time, not query time, and is separate.
- **Peer-reviewed (SIGMOD/PACMMOD 2026, arXiv 2603.23710, Table 1)** — pgvector HNSW architecture: *"Tuple-based
  storage: Each neighbor reference is a 6-byte TID. Nodes are stored as tuples, with header overhead, in 8KB pages"*;
  *"Scattered page access: The neighbors are stored as TIDs pointing to arbitrary pages. Random I/O amplification:
  each hop multiplies page overhead by neighbor fanout."* — this is a Postgres index over 8 KB pages, not an
  in-RAM pointer graph.
- **Vendor engineering (AWS Aurora blog):** speaks of the index *spilling to disk* — i.e. spilling is a supported
  state, and the complaint is degradation, not failure.

**By contrast Position A's sources are the ones that say "must":** AWS (*"an HNSW index must stay memory-resident"*),
Google AlloyDB, ParadeDB, Pinecone, ClickHouse — **every one is a performance recommendation phrased as a
requirement**, and none claims an error or a wrong result. ⚠️ **Note the sharp edge: Position A's own cited source
does not say what it is cited for.** The Next Platform article (2026-09-22) says *"the moment nodes spill to disk,
latency is dominated by page faults"* and then explicitly proposes **tiering** — *"hot vectors in RAM-backed HNSW,
cold vectors quantized on disk or in object storage."* That is Position B, with a latency caveat.

**Plain-language mechanism.** Think of it like a B-tree: Postgres keeps an 8 KB page cache. Finding neighbours in HNSW
means hopping from node to node, and each node lives on some page. If that page is in cache, the hop is a memory read.
If not, Postgres reads the 8 KB page from disk first. The hop count is fixed by `ef_search` (default 40); a cold index
just makes each hop cost a disk read. Crucially the hops are **scattered** — the next node is on a random page, so the
OS cannot read ahead the way it does for a table scan. That is the "cliff": not that the query fails, but that ~40
random 8 KB reads replace ~40 memory reads. A cached index is fast; a nearly-cold index is tail-latency-dominated.

---

## 2. The capacity-planning model

### 2.1 The two numbers that are not the same number

| quantity | what it is | scales with |
|---|---|---|
| **index footprint** (`pg_relation_size`) | bytes on **disk** | **total** vectors |
| **resident set** (shared_buffers hits) | bytes in **RAM** | **hot working set** |

Position A's error is to price the first, in units of the second, multiplied by the tenant count.

### 2.2 Per-vector footprint

```
footprint_per_vector  ≈  1.1 × (4 × d + 8 × M)   bytes        [OpenSearch/Lucene HNSW estimator,
                            └ vector ┘   └ graph ┘              via The Next Platform 2026-09-22]
```
At **d = 384, M = 16**: `1.1 × (1,536 + 128) = 1,830 B ≈ 1.79 KB`.

**Cross-checks (measured):**
| source | corpus | measured | vs formula |
|---|---|---|---|
| pgxn pgcontext harness | 1,000,000 × 384, pgvector 0.8.5, m=16 | index **1.91 GiB** → **2.05 KB/vec** | 1.12× |
| pgxn pgcontext harness | 100,000 × 384 | index **195.3 MiB** → 2.05 KB/vec | 1.12× |
| **our** `GRAPH.MEMORY USAGE` | 33,580 × 384, **FalkorDB** | 51.6 MB raw + 44 MB index = 95.6 MB → **2.85 KB/vec** | 1.56× |

⚠️ **The "1.56× measured" figure in §12.1 is not a pgvector measurement** — it is FalkorDB's `GRAPH.MEMORY USAGE`.
The two engines store the graph differently (the SIGMOD paper's *"space amplification: page-level overhead (headers,
alignment) reduces effective storage density"* is a pgvector-specific effect FalkorDB does not have). **Do not carry
the 1.56× multiplier onto Postgres.** Plan on **2.0 KB/vector** for pgvector at d=384/M=16, with the formula as the
lower bound.

### 2.3 Our numbers, plugged in

Measured ratio today: **33,580 embeddings / 25,000 nodes = 1.3432 embeddings per node.**

**Reading 1 — 2.5M nodes is the PRODUCT target (2.5M nodes in total):**
```
total embeddings  = 2,500,000 × 1.3432        ≈  3,358,000
per-tenant share  = 3,358,000 ÷ 1,000         ≈      3,358
per-tenant index  = 3,358 × 2.05 KB           ≈    6.9 MB   (disk)
total index size  = 3,358,000 × 2.05 KB       ≈    6.9 GB   (disk)
```

**Reading 2 — 2.5M nodes is ONE TENANT's target (1,000 × 2.5M = 2.5 billion nodes):**
```
per-tenant embeddings = 2,500,000 × 1.3432            ≈  3,358,000
per-tenant index      = 3,358,000 × 2.05 KB           ≈    6.9 GB   (disk)
total embeddings      = 1,000 × 3,358,000             ≈  3.358 billion
total index size      = 3.358e9 × 2.05 KB             ≈    6.9 TB   (disk)
```

**What each reading actually means — and where Position A goes wrong:**
- **Reading 1 (2.5M total):** the entire product's vector index is **~7 GB of disk**, and the whole thing fits in a
  Supabase 2XL's `shared_buffers` at once. This is the reading §5's cost table assumes (*1,000 users = 1 TB*).
- **Reading 2 (2.5M/tenant):** **6.9 TB of disk.** ⚠️ **The 7 TB figure is arithmetically SOUND here** — each tenant
  is charged for its own vectors, not anyone else's. Supabase's largest compute (16XL, 256 GB RAM) cannot hold that
  index resident; but it does not need to: **the index is on disk, and RAM prices the hot set.** The per-query
  working set is one tenant's index — **6.9 GB**, not 6.9 TB. If *all 1,000 tenants were simultaneously hot*, RAM
  would be ~7 TB — but *"every tenant hot at once"* is an operating assumption about demand, **not a property of the
  index**, and it is the same assumption that would make any data structure expensive.

⚠️ **So the defect is not the multiplication — it is three other things, and they must be stated correctly:**
1. **Classification.** 7 TB is a **disk** number. Position A calls it RAM. (7 TB of Supabase disk at
   **$0.125/GB/mo** — `STORAGE-ARCHITECTURE.md` §5 — ≈ **$875/mo**, inside that section's **$9/user × 1,000 =
   $9,000/mo** budget; the RAM version is prohibited by the platform.)
2. **The silent scale flip.** §12.1 silently assumes **2.5M nodes per tenant** (⇒ ~6.9 GB of index per tenant)
   while §5 assumes **1,000 users = 1 TB total** (⇒ ~1 GB per user). **~7× apart on the index alone** (~12× if the
   raw vector column is counted: 6.9 GB index + 5.2 GB raw ≈ 12 GB/tenant) — and the document never states which
   product it is describing. (Review finding B1.4, whose own "~14×" is not reproducible from these components.)
   **The document must fix the reading before any number in it is usable.**
3. **"Total" read as "resident".** The claim *"every vector is a tax on every query forever"* is false on the
   page-cache model: an unread vector's pages are neither read nor resident.

**And §12.1's "§5's arithmetic is invalidated" conclusion is retired:** §5's disk model was right. The correct
restatement is *"§12.1 is the hot-set sizing input to §5, not a refutation of it."* ⚠️ **And §12.1's 7.1 GB mixes
bases on top of the scale flip:** it applies the rejected FalkorDB rate (~2.85 KB/vec) to the **node** count (2.5M),
not the **embedding** count. At 2.05 KB/vec × 3.358M embeddings the corrected figure is **6.9 GB** — the two errors
partly offset, which is exactly why the figure looked plausible.

### 2.4 What actually binds

| resource | bound by | Reading 1 (2.5M total) | Reading 2 (2.5M/tenant) |
|---|---|---|---|
| RAM | **hot** working set × 2.05 KB, + buffer churn headroom | **~7 GB** (all hot) | **~6.9 GB per hot tenant** — only hot tenants count |
| **disk** — index only | total embeddings × 2.05 KB | **~7 GB** | **~6.9 TB** |
| **disk** — index **+ raw vector column** | the above + total embeddings × 1.54 KB | **~12 GB** | **~12 TB** |
| **IOPS** | random page reads on cold traversals | **the real unknown** (§3) | **the real unknown** (§3) |
| **planning time** | partition count (1,000 partitions) | **unmeasured — must test (M2)** | **unmeasured — must test (M2)** |

**⚠️ The binding constraint moves from RAM to IOPS + planning, exactly as §2 already argued for the rest of the
store.** Position A was the one exception claimed against that rule; it does not hold.

---

## 3. The latency penalty when the index is not resident

### 3.1 The honest headline

**Claim 2 [HIGH — of the *absence*; the magnitude below is ⚠️ emerging].** There is **no credible public measurement of
pgvector HNSW p95/p99 on a genuinely non-resident index.** This is a *finding of absence*, verified by **reading two
primary sources directly** (not by inference):
- The benchmark harness that has a **"Cold-cache lane (100k corpus)"** lists it as **"Not yet measured."**
- The SIGMOD study explicitly **removes** the case: *"[we] utilized the `pg_prewarm` extension to fully load both the
  table and the index into the database buffer cache before each experimental run. **The scope of this work focuses
  on in-memory use cases.**"* — shared_buffers = 64 GB, maintenance_work_mem = 64 GB.
- The widely-quoted **96 ms p90 vs 24 ms** figure is **OpenSearch's designed disk-based mode** — a different engine
  with a purpose-built disk mode, **not** a pgvector spill measurement.

**⇒ Any number Position A or B quotes for "the pgvector spill penalty" is currently an extrapolation.**

### 3.2 What the evidence does support

| evidence | magnitude | kind | tier |
|---|---|---|---|
| pgxn churn lane: first query after churn vs steady (100k × 384) | **1,211 / 1,727 ms** vs sub-ms | **measured** (pgvector-family) | ⚠️ single-source — single suite, single trial, churn not cold-cache |
| OpenSearch disk-based mode vs in-memory | p90 **96 ms vs 24 ms** (4×) | **measured**, different engine | ⚠️ single-source — verify when a second disk-mode benchmark is available |
| pgxn harness, pgvector lane at 1M × 384 (warm, fixed ef) | 79 ms p50 | **measured** (scale, **not** spill); **third-party harness — NOT ours** | ⚠️ single-source |
| AWS / Google / ParadeDB / ClickHouse / particula | "must stay resident", "ms → seconds" | **vendor claim / secondary blog** | ⚠️ vendor-claimed |
| Pinecone: throughput drops **>10×** when index exceeds RAM | >10× | **vendor marketing** (competitor to pgvector) | ⚠️ vendor-claimed |
| Microsoft DiskANN: 1B points on 64 GB + SSD, 95 %+ recall | ~1B @ 64 GB | vendor/research claim | ⚠️ emerging |

**The planning range — an EXTRAPOLATION, not a measurement.** A non-resident pgvector index should be
**high-hundreds of ms to low seconds p99** under load, against **single-digit-ms** p50 when resident, i.e. a
**~10²–10³× tail penalty** dominated by page faults. **For our access pattern this is affordable** (a
multi-second agent reasoning loop), and it matches §12.2b's conclusion — but it is **an inference from a different
engine (OpenSearch) and a churn lane, not a measurement of us.**

#### Required Evidence

| claim | would be **confirmed** by | would be **refuted** by |
|---|---|---|
| Tail penalty is hundreds-of-ms–seconds, i.e. ~10²–10³× p50 | **M1**: 3.36M × 384 on a Supabase instance, index > `shared_buffers`, p50/p95/p99 as residency falls | a measured p99 within ~2× of resident — i.e. the page cache absorbs it |
| The penalty is **invisible in our agent loop** | **M3**: end-to-end retrieval-to-answer latency with a cold index vs a `pg_prewarm`ed one | a cold index pushing a multi-second loop past its budget |
| Recall is **unaffected** by residency | **M1** + a recall-vs-residency curve against exact search | recall degrading with residency (would mean the traversal is truncating, not just slowing) |

---

## 4. `shared_buffers` — the conflation, resolved

**Claim 3 [HIGH].** The index is **not** required to be in `shared_buffers` specifically. It needs to be in **RAM**,
and Postgres has **two** RAM layers (Supabase compute for reference: 2XL = 32 GB, 4XL = 64 GB, up to 16XL = 256 GB):

1. **`shared_buffers`** — Postgres's own buffer pool. The buffer manager checks here first. Default recommendation is
   ~25 % of RAM (pgvector README points at PgTune); Postgres docs note >40 % is usually not better.
2. **The OS page cache** — if the page is not in `shared_buffers`, Postgres issues a filesystem read; if the kernel has
   the page cached, no disk I/O occurs (it is copied into a `shared_buffers` slot).

**Both pools count.** An index that has spilled out of `shared_buffers` but is still in the page cache is fast; one
that is in neither costs a disk read per hop. **The practical sizing rule is therefore `shared_buffers` +
*expected page-cache residency* vs the hot working set** — and because the two caches compete for the same physical
RAM, they must be sized **together**, never as if `shared_buffers` were the only cache.

**A lever that matters here:** `pg_prewarm` **is available on Supabase** (verified in Supabase's own extension list).
It is the standard mechanism the academic study used to pin an index into the buffer cache, and it can warm either the
whole index or just a hot subset. **A per-tenant partition makes `pg_prewarm` a targeted operation** (warm the
tenant's partition index on first query of a session) — this is the write-back/heat mechanism §12.5 is missing, and
`pg_prewarm` already exists.

**Caveat that survives even when fully cached [MEDIUM]:** Google AlloyDB documents that even a **fully cached**
pgvector HNSW pays buffer-manager overhead (pin/unpin, lock acquisition, buffer-table lookups); the SIGMOD study
measures **up to 10×** system overhead for pgvector vs a standalone library at *matched, fully-resident* settings.
So "it fits in RAM" does not mean "it is as fast as a vector DB".

---

## 5. Partitioning — how it interacts with HNSW

**Claim 4 [HIGH] for the mechanism.** Per-tenant **declarative list partitioning** gives each tenant **its own HNSW
index**. Postgres **partition pruning** excludes non-matching partitions from the plan, so a query for one tenant
traverses **only that tenant's index** — reading only that partition's pages.

- **pgvector's own README recommends exactly this** (Multitenancy section, verbatim): *"sharing an approximate index
  between tenants means vectors from one tenant can affect recall (and speed) for other tenants. **For tenant
  isolation, use list partitioning or separate tables.**"* It also names partitioning as the answer to filtering *"by
  many different values."*
- **Postgres docs:** *"partitioning can make heavily used parts of indexes more likely to fit in memory."*
- **⇒ Only the hot partitions need to be resident.** Cold tenants' index pages simply stay on disk and are never
  read. This is the mechanism that makes Position B true, and it is a pure win on recall (each graph is smaller and
  tenant-homogeneous, so `ef_search` explores a denser, more relevant neighbourhood).

**⚠️ Two caveats that must be measured, not assumed:**
1. **Pruning requires a constant the planner can use.** An **RLS predicate** (`auth.uid()`) is **not a plan-time
   constant**, so the planner cannot match a partition — the tenant must be passed as a **literal or a parameter**
   and the plan verified (`EXPLAIN` must show only one partition scanned). §4's "tenant-scoped rows via RLS" and
   "per-tenant partitioning" are **two different physical designs**; adopting partitioning means pinning how the
   tenant id reaches the query. (Review finding B1.10.)
2. **1,000 partitions has a cost** — planning time and relcache lookups grow with partition count, and Postgres opens
   per-partition relations even when pruning. Supabase's own partitioning doc gives **no** partition-count guidance
   and warns *"there is no real threshold... partitions introduce complexity."* **This is a benchmark we must run**
   (1,000 partitions × an ANN query, p50/p99, `EXPLAIN (ANALYZE, BUFFERS)` showing pruned partitions).

**Alternative if 1,000 partitions misbehave:** a **partial index keyed on a small set of shards**
(`CREATE INDEX ... WHERE tenant_shard = 7`) gives partial-HNSW without 1,000 relations — this is the shape Hindsight
used (*"split ANN per `fact_type` to use partial HNSW indexes"*), and it caps planning cost at ~`shard_count`.

---

## 6. Genuinely disk-resident options, and Supabase availability

**Claim 5 [HIGH].** **Supabase does not offer `pgvectorscale`.** Verified three ways:
1. **Live fetch of Supabase's own extension list** — `vector`, `pg_partman`, `pg_prewarm`, `pg_cron`, `rum`, `pg_repack`,
   `index_advisor`, `pg_plan_filter`, … **no `vectorscale`/`pgvectorscale`.**
2. **Two open Supabase feature requests for it** — discussions **#27474** (*"there does not seem to be a way to
   integrate it directly through the SQL editor as a custom extension"*) and **#29095**.
3. Supabase's extension list says non-preinstalled extensions can come via `database.dev` — but `pgvectorscale` is
   **Rust/C** (PGRX), not pure SQL, so that path does not apply.

| option | what it is | availability | latency evidence |
|---|---|---|---|
| `pgvector` HNSW | page-cached index | **Supabase ✓** | §3 |
| `pgvector` `halfvec` | ½ the vector bytes; at **M=16** the *index* reportedly falls only **~1.4×** (`REVIEW-CONSOLIDATED-2026-09-23.md` §C recomputation) — ⚠️ **the §2.2 estimator predicts ~1.86×; the two disagree and neither is measured** | **Supabase ✓** | n/a (storage) |
| `pgvector` binary quantization + rerank | **~8.9× smaller** index at M=16 (`REVIEW-CONSOLIDATED-2026-09-23.md` §C recomputation; §2.2 estimator ≈ 9.45× — consistent); **recall unmeasured on our data** | **Supabase ✓** | n/a |
| `pg_prewarm` | pins a relation/index into buffer cache | **Supabase ✓** | n/a (a lever, §4) |
| partition / partial HNSW | per-tenant or per-shard index scope | **Supabase ✓** | n/a (a lever, §5) |
| **pgvectorscale / StreamingDiskANN** | purpose-built disk index, SBQ compression, filtered labels | ✗ **NOT on Supabase** — Timescale/Tiger Cloud, DigitalOcean, self-host | defaults `num_neighbors=50`, `query_rescore=50` (pgvectorscale README) |
| **Amazon S3 Vectors** | object-storage-backed index | AWS only, **GA Dec 2025**; 2B vectors/index, 10k indexes/bucket | **~100 ms** frequent query |
| Microsoft DiskANN | disk ANN | not a Postgres extension | 1B points / 64 GB + SSD, 95 %+ recall |

**⇒ Position B's claim — "on Supabase this is achieved by partitioning, not by a disk-resident index" — is
correct as of today**, and the three levers in §12.2b hold their ranking. `pgvectorscale` is worth revisiting if
Supabase ships it (**#27474 / #29095**), and **S3 Vectors is a live escape hatch at ~100 ms** *if* we ever accept a
second system and dual writes.

---

## 7. Reproducing derived state when the derivation is non-deterministic

**Claim 6 [HIGH].** Convergent practice, two independent literatures:

**(a) Embeddings specifically** — store the vector **together with** `model_id` + `model_revision` + a **content hash
of the embedded text**; **pin the model version**; on a model change, **re-embed the whole corpus into a separate
index** and flip; **never mix versions in one index** (vectors from different model versions are not comparable).
*(dev.to / thesovereigninstitute / milvus / levelop / tianpan — five independent practitioner sources agreeing.)*

**(b) Event sourcing, when the derivation is non-deterministic** — **record the output as an event**; replay consumes
the **stored** value and must **not** re-run the model. *"The past cannot be recomputed."* Deterministic replay
(byte-identical state on the same log) is the stated requirement (Fowler; Azure Architecture Center; AWS Prescriptive
Guidance; Akka data-integrity spec; arXiv 2602.23193), and the LLM-agent literature reaches the same conclusion in
the affirmative: **the LLM output itself is the event.**

**⇒ Answer to the "store or regenerate" question: store.** Regeneration is only defensible as a *migration*, never as
the replay path, because it is a function of a model that is mutable, remote, and can be withdrawn.

**⚠️ Our code contradicts this today.** `tortoise/embeddings.py` pins both the model id and the HF revision
(`EMBEDDING_MODEL_REVISION = "5c38ec7c..."`) — good. But `projection/__init__.py:3326-3343` **strips `embedding`
from the journal-derived snapshot** with the comment *"the replay re-derives it"*. **Replay that re-derives is a
re-run, not a replay** — so the architecture's own invariant (*"derived = replay(journal), never recompute"*,
§3) is false for the embedding field, and a rebuild after the model revision changes produces a **different graph**.
The review's B1.6 predicted exactly this; the convergent practice says **the embedding belongs in the journal
payload**. Cost: ~1.5 KB/point (`4 × dims + 8`) — against `STORAGE-ARCHITECTURE.md` §2/§6's own **580× per-GB
disk-vs-RAM** figure, an acceptable disk cost.

---

## 8. Source confidence summary

Tier vocabulary is the research skill's canonical set: **High** (3+ independent categories) / **⚠️ emerging** (2
categories) / **⚠️ single-source** (1 source) / **⚠️ hypothesis** (LLM memory only).

| # | claim | sources | independence | tier | confidence |
|---|---|---|---|---|---|
| 1 | HNSW index is an ordinary page-cached index; does not need to fit RAM; results still correct | pgvector README FAQ · SIGMOD/PACMMOD 2026 (arXiv 2603.23710) · AWS Aurora blog | 3 categories (official docs / peer-reviewed / vendor engineering) | **High** | **0.8** |
| 2 | The penalty is a random-page-fault latency cliff; each hop can be a disk read; no prefetch | arXiv 2603.23710 · VLDB vol.18 p4710 · AlloyDB · ClickHouse/ParadeDB | 3 categories | **High** | **0.8** |
| 3 | **No public measurement of pgvector p95/p99 on a non-resident index** | pgxn "Not yet measured" · SIGMOD study's explicit `pg_prewarm` + in-memory scope | 2 categories, both read directly | **High** *(the absence)* | **0.8** |
| 4 | Spill penalty magnitude ≈ 10²–10³× tail vs resident; hundreds of ms–seconds p99 | pgxn churn lane (measured) · OpenSearch disk mode (measured, other engine) · vendor blogs | 2 measured + several vendor | ⚠️ emerging | **0.5** |
| 4b | pgxn pgvector lane at 1M × 384: 79 ms p50 warm | pgxn pgcontext harness (third-party; provenance of the pgvector column not disclosed by the page — see §9.5) | 1 source | ⚠️ single-source | **0.3** |
| 5 | `shared_buffers` is not required; shared_buffers + page cache are one RAM pool | Postgres docs · MS/Heroku/TigerData engineering · pgvector README | 3 categories | **High** | **0.8** |
| 6 | Even **fully cached**, pgvector HNSW pays engine overhead vs a standalone library — **up to 10×** (SIGMOD) | SIGMOD/PACMMOD 2026, Figure 1 (quantitative: up to 10× vs library) | 1 source | ⚠️ single-source — verify when a second fully-resident library-vs-DBMS benchmark exists | **0.3** |
| 6b | A fully cached index **still** pays buffer-manager cost (pin/unpin, locking, buffer-table lookup) | AlloyDB blog (**qualitative only** — no magnitude, no library comparison) | 1 source | ⚠️ single-source | **0.3** |
| 7 | Per-tenant partitioning prunes to one index; only hot partitions need residency | pgvector README (Multitenancy) · Postgres docs (pruning) · index-management/DeepWiki/uplatz | 3 categories | **High** | **0.8** |
| 8 | `pgvectorscale` is **not** available on Supabase | live Supabase extensions list · Supabase discussions #27474/#29095 · Supabase features page | official primary + 2 independent signals | **High** | **0.9** |
| 9 | S3 Vectors is GA with 2B vectors/index at ~100 ms | AWS GA announcement · AWS S3 vectors page · InfoQ · CloudZero | official primary + 3 secondary | **High** | **0.8** |
| 10 | Footprint ≈ 1.1 × (4d + 8M) B/vector; measured 2.05 KB at d=384/M=16 | OpenSearch formula · pgxn measured index sizes | 2 categories | ⚠️ emerging | **0.5** |
| 11 | Our "1.56× measured" figure is a **FalkorDB** measurement, not pgvector | §12.1 (GRAPH.MEMORY USAGE) · SIGMOD space-amplification finding | 2 categories | **High** | **0.8** |
| 12 | Store the embedding + model pin + content hash; replay uses the stored value | 5 practitioner sources (embeddings) · Fowler/Azure/AWS/Akka + arXiv 2602.23193 (event sourcing) | 2 categories, convergent | **High** | **0.8** |

---

## 9. What is genuinely UNKNOWN or vendor-claimed-only

1. **pgvector's actual p95/p99 with a non-resident index.** No public measurement exists. Everything circulating is
   either a different engine (OpenSearch, DiskANN) or a vendor assertion. **Unknown.**
2. **Recall behaviour under partial residency.** Recall *should* be unaffected (same graph, same `ef_search`) — but
   nobody has published a recall-vs-residency curve for pgvector. **Unknown, and it matters if we ever tier.**
3. **The IOPS bill.** At our scale the cliff's *cost* is IOPS, and Supabase IOPS is tier-coupled (compute-capped ∪
   disk-provisioned). No budget model exists yet. **Unknown.**
4. **1,000-partition planning overhead on Supabase.** **Unknown — must benchmark.**
5. **The `pgxn` harness's pgvector column — provenance and mixed lanes.** The table is **pgContext's** (a competing
extension), single-trial, on an M4 Pro; the churn lane shows no pgvector column, and the page does not disclose how
  the 1M × 384 pgvector figure in §3.2 was produced. **Treat all pgxn figures as indicative, third-party, and
  unverified — not as our measurements.**
8. **Whether `halfvec` shrinks the index by ~1.4× or ~1.86×** — the review's recomputation and §2.2's own estimator
   disagree, and **neither is a measurement.** (The binary-quantization figures do agree, ~8.9× vs ~9.45×.)
9. **Which reading of "2.5M nodes" the rest of the architecture document means.** §5 implies total, §12.1 implies
   per-tenant. **This is the one thing that must be fixed before any figure in `STORAGE-ARCHITECTURE.md` is usable.**
6. **DiskANN's "1B on 64 GB" and S3 Vectors' "~100 ms"** are vendor claims. Plausible, unverified by us.
7. **Quantization recall cost on our data** (`halfvec`, binary + rerank) — §12.2b already flags this as unmeasured.

---

## 10. Recommendation

**Adopt Position B, and restate §12.1 rather than deleting it.** ⚠️ **Cycle-1 correction applied:** the 7 TB figure
is **arithmetically sound** under the per-tenant reading and **is a disk number**; the refutation is the *model and
the unstated scale flip*, not the multiplication (§2.3).

1. **Replace Position A in §2 and §12.1** with the corrected model: *the HNSW index is disk-backed and page-cached;
   total vector count is a **disk** cost; RAM prices the **hot working set***. Delete the claim that §5's arithmetic
   is invalidated. Delete the "7 TB" figure — **not because it is wrong (it is arithmetically sound under Reading 2)
   but because it is unscoped**: it silently mixes a per-tenant target with a whole-product total, and a figure whose
   meaning is ambiguous will be reused incorrectly.
2. **Restate the capacity model with the formula in §2.2**, using **2.05 KB/vector** at d=384/M=16 (not the 1.56×
   FalkorDB multiplier). Plug in our numbers as §2.3 — **and state the scale reading explicitly** (2.5M nodes
   total, or per tenant), because the two differ by 1,000× on disk and the document currently mixes them.
3. **Fix the citation.** The p90 96 ms / 24 ms figure is **OpenSearch's disk mode**, not pgvector's spill penalty —
   label it as such, or drop the "invisible in an agent loop" argument to an explicit **must-measure** (§12.8 item 1
   already says this; the citation undercuts it).
4. **Keep the three levers and their order** (partition → don't embed identifier-only text → `halfvec`). Lever 1 is
   now supported by pgvector's own documented multitenancy guidance, which is a stronger footing than the Hindsight
   analogy.
5. **Add `pg_prewarm` as a fourth lever** — it is available on Supabase, it is how the academic benchmark pins an
   index, and per-tenant partitions make it a targeted heat operation (§4/§5).
6. **Record `pgvectorscale` as unavailable on Supabase with the three-way evidence**, and name **S3 Vectors (~100 ms,
   GA)** as the escape hatch if we ever accept a second system.
7. **Fix the journal.** Put `embedding` in the journal payload (§7). Replay must reproduce, not re-derive.

**Before committing, measure on our own stack (all four are cheap):**
- **M1 — spill curve:** 3.36M synthetic 384-dim vectors on a Supabase instance, index > shared_buffers; p50/p95/p99
  and `pg_stat_statements` buffer-hit ratio as residency varies. **This is the number nobody has.**
- **M2 — partition plan shape:** 1,000 partitions, `EXPLAIN (ANALYZE, BUFFERS)` proving one-partition prune, plus
  planning time and p99 at 1,000 partitions. Decides partition vs partial-index-shard.
- **M3 — our p50/p99 at 1M×384 with `pg_prewarm` on vs off**, to price the prewarm lever.
- **M4 — quantization recall** of `halfvec` and binary+rerank against our own eval set.

### Adoption gate (Step 5.6)

`Adoption gate: **adopt** — no recorded decision contradicts it.` Checked: the **tenancy decision** (§4, owner
2026-09-23 — *one database, tenant-scoped rows*) is **not** contradicted; declarative partitioning is a physical
layout *within* one database. The one live interaction is *how the tenant id reaches the planner* — an RLS predicate
is not a plan-time constant, so adopting partitioning requires pinning that (§5 caveat 1, review B1.10). That is a
**mechanism follow-on**, not a conflict with the decision. `~/.pi/agent/state/DECISION-LEDGER.md` contains no ruling
on the vector-index memory model, and `REVIEW-CONSOLIDATED-2026-09-23.md` records D1 as an **open research question**
— so the finding is adopted and recorded, not escalated.

> **OVERRIDES:** the vendor default *"an HNSW index must stay memory-resident"* (AWS/Google/Pinecone/ClickHouse) —
> it is a latency recommendation, not a correctness requirement; pgvector's own documentation states the index
> **need not** fit in memory, and the design keeps the index disk-backed and partitions by tenant so RAM prices the
> hot working set rather than the total.

### Memory not persisted

`TORTOISE_API_KEY` is not set on this machine (`status: not_configured`) — claims were **not** written to the
epistemic graph. Re-run Step 5.4 when a key is available.
