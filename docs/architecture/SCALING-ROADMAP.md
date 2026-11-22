---
title: "Scaling roadmap — what comparable memory systems do, and the free prep work"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-21
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: "SCALING-ROADMAP.md, PLAN-private-beta-and-scale.md, issue #14216"
---

# Scaling roadmap — what comparable memory systems do, and the free prep work

**Date:** 2026-11-21 · **Status:** research + roadmap · **Owner ask:** *"research how competitors (memory systems) approach
this in their codebase … what can we learn about SOTA practices and start to build in the right direction of an
infrastructure that can handle load. We can have things ready for scaling without paying yet."*

**Search:** `web_search model=sonar` — rung 2 (rung 1 `seo-intelligence` unavailable on this box) — 3 queries attempted /
3 returned. Sources are vendor papers, engineering blogs and architecture references; they are **third-party claims, not
reproduced here**.

⚠️ **Provenance, stated per claim, because it is uneven.** ONE source is named by its URL:
`docs.mem0.ai/core-concepts/memory-evaluation`, and it backs only Mem0's **three storage layers, six-stage pipeline and four
parallel retrieval signals**. Everything else is a summary of vendor papers/blogs with **no URL recorded here**: Mem0's **< 50 ms
ack, queue-depth autoscaling, partitioning and search percentiles**, and every Zep, Hindsight, Supermemory and turbopuffer figure
in §1 and §2.5. That is not a formality — the Mem0 material WAS checked against its one link and came back wrong in four cells
(the storage-layer description and the durable-store count in §1/§2, the parallel-legs count and the "single source" conclusion
in §2.5, plus their twins in `PLAN-private-beta-and-scale.md` §2–§3). Re-source a figure before quoting it outside this document.

---

## 1. What the comparables actually do

### Mem0 — separate the write plane from the read plane

- **Topology:** a **vector store** holding the memory text, embeddings and metadata as *"primary fact storage"*, a **graph/entity
  store** for relationship-aware retrieval, and a **SQL** database holding an ADD-event history log plus a rolling message window.
  *That is nearly our shape* — Postgres record + graph + vectors, except that **their system of record is the vector store and the
  SQL side is the audit trail**, not the other way round.
- **Writes are asynchronous**: an add returns in **< 50 ms** and the real work (fact distillation, deduplication, embedding,
  entity linking, temporal reasoning) happens on a queue. Workers autoscale on **queue depth**, and tenant ordering is kept by
  **partitioning**.
- **Reads are the latency-critical plane**: the paper reports search at **p50 148 ms / p95 200 ms**.
- Ingestion is a **six-stage pipeline**, with the slow stages explicitly allowed to complete later
  (`docs.mem0.ai/core-concepts/memory-evaluation`).

**Take-away:** they never make a *read* wait for extraction. The expensive work is priced into the write, and even there it is
acknowledged rather than performed inline.

### Zep — a dedicated graph service in front of the store

- **Konig**, their production graph database service, does **hot-graph caching** and **write-ahead-log replay**; reported
  **p95 retrieval < 100 ms**, end-to-end **< 200 ms** at large graph counts.
- Ingestion is **asynchronous**: classification → entity/fact extraction → graph construction → hybrid retrieval, and the docs
  state plainly that *"newly extracted facts may become available after a short processing delay"*.
- Graphiti (the open framework) does **incremental** graph construction and hybrid semantic/keyword/graph retrieval — the same
  three legs we run.

**Take-away:** our record→graph projection **is** their WAL replay. The difference is that they run it as a **service** with a
**hot cache in front**, and they publish the delay rather than hiding it.

### Inference serving — the embedding step is its own tier

- **Separate API from inference workers with a queue**; scale on **queue depth, in-flight requests or tokens/sec — not CPU**;
  keep **warm workers** for latency-sensitive traffic.
- **Continuous batching**, and **small micro-batches for interactive traffic vs deep queues for backfills** — two deployments,
  not one compromise.
- **Latency-budget decomposition**: per-stage budgets, **hard maximum batch waits**, admission control, warm pools, and
  **separate p50/p95/p99** — with the explicit warning that *"batching improves efficiency but can worsen tail latency"*.

**Take-away:** our current design violates the first rule of all three: **the query's embedding runs inside the request**, on
the same shared cores that serve everything else (4 since `#14156`). Every competitor puts that step behind either a dedicated worker, a
purpose-built index, or an external API.

---

## 2. Where we actually stand (measured 2026-11-20/21)

| property | ours today | the comparable's posture |
|---|---|---|
| system of record | **Postgres record** (`STORAGE-ARCHITECTURE.md` §2.0 owner ruling) | Mem0: vector store primary; SQL = ADD history log — ⚠️ **not the same** |
| derived search layer | **graph cache + pgvector** | Zep: Konig with hot-graph cache ✅ same shape |
| hybrid retrieval | **semantic + keyword + graph** | Zep: the same three legs; Mem0: those three plus a **temporal** signal (four in total — its `Entity Search` IS the graph leg) ✅ same three |
| **write path** | synchronous through the API | **queued, acked < 50 ms**, workers autoscaled ✗ ours is inline |
| **query embedding** | **inside the request**, 2–4 s on the box's shared cores (4 since `#14156`) | dedicated worker tier / external API ✗ |
| **batching** | none for interactive queries | continuous batching + max-wait bound ✗ |
| eviction / working set | executor + usage signal merged (`#10976`, `#14011`) | hot-graph cache ✅ we have the parts |
| admission control | wait budgets + refusal accounting | latency budgets + admission control ✅ we have the shape |
| **stage metrics** | present on **error** paths only (`stages_ms` in a 504 body) | per-stage p50/p95 **always** ✗ |
| scaling unit | one process, one machine | queue depth → replicas ✗ |

Two structural gaps explain most of the ✗ rows above:

1. **The read path does the expensive, batchable work synchronously.** A single query embed costs ~2–4 s here; batched it is
   **0.36 s/item of THROUGHPUT** (32 items in 11.6 s — that 11.6 s is the *batch's* wall time, not a request's latency), i.e.
   **5.3×** on the same cores (1.9 s vs 0.36 s per item, both arms of the same paired bench) — a difference that is pure
   scheduling, not hardware. ⚠️ Turning throughput into latency needs a **maximum batch size**, not just a collection window
   (A3 below).
2. **The write path is inline.** We already *have* the queue in embryo: `journal_event` is an append-only log and the
   projection is a fold over it. What is missing is a **worker that consumes it on its own schedule**.

The remaining two ✗ rows — success-path stage metrics and the scaling unit — are neither gap: they are Phase A's **A4** and **A5**.

---

## 2.5 Five comparables, in detail — and what to take from each

### Hindsight (Vectorize) — the closest match to us, and the most useful

- **Storage:** **PostgreSQL + pgvector**. Not a bespoke vector engine — the same stack we chose in `STORAGE-ARCHITECTURE.md` §12.1c.
- **Retrieval:** *"Recall combines **vector, keyword, graph, and temporal** search **in parallel**"* — we run three of those
  four legs and **already dispatch them concurrently** (`degradation_chain` → `ThreadPoolExecutor(max_workers=3)`, 500 ms collective
  cap, landed in `4abff72cbb`, 2026-08-04). The stage still measures **1,199 ms**, so what remains is contention and cap overrun, not sequencing — a
  distinction the plan's D2 has to get right, because it changes what `#14192` should build.
- **The sentence that should reorganise our roadmap:** *"The design shifts **LLM cost to writes** … Embeddings, fact
  extraction, and graph structures are **prepared during retention to reduce retrieval-time work**."* We do the exact
  opposite: the query's embedding is computed **inside** the read.
- **Latency is set by the slowest branch, not the sum** — *"Recall latency is determined by the slowest parallel branch
  rather than the sum of all branches."* Our stages are a chain (`pre_handler → embed_encode → retrieval → ep → ordering`), so
  we pay **the sum** — but ⚠️ **only the three legs INSIDE `retrieval` can overlap at all**: `retrieval` is 1,199 ms of an
  11,354 ms chain, while `pre_handler` (5,996 ms in the slow trace) and `embed_encode` (2–4 s) are not branches of anything, so parallelising the legs
  buys at most a fraction of 1.2 s, never the ~5.4 s. Hindsight's 100–600 ms is the payoff of the bullet above — **cost moved to
  writes** — not of parallelism alone.
- **Bounded graph traversal** and **hierarchical compression** (facts → observations → mental models) — the compression ladder
  is shape-identical to our Point → Object/Event → goal map.
- **Memlake:** durable data lives in **object storage**, and **servers are disposable caches**.
- **Published numbers:** recall **100–600 ms**, reflect 800–3,000 ms, retain 500–2,000 ms per batch.

**Take:** parallelise the legs; move cost to writes; bound traversal; keep servers disposable. Their published recall budget
(100–600 ms) is the number we should be able to defend.

### Mem0 — the write plane as a product

- Vector store as **primary fact storage** + graph/entity store, with SQL as an ADD-event history log. Adds ack in **< 50 ms**;
  extraction/embedding/dedup/entity-linking run on a
  **queue**, workers autoscaled on **queue depth**, tenant ordering by **partitioning**. Search **p50 148 ms / p95 200 ms**.

**Take:** acknowledgement ≠ completion. A write should not wait for its embedding.

### Zep — the derived layer as a service

- **Konig**: a standalone graph service with **hot-graph caching** and **write-ahead-log replay**; **p95 < 100 ms**,
  end-to-end < 200 ms. Extraction is asynchronous and the delay is *stated*.

**Take:** our record→graph projection **is** their WAL replay. The difference is packaging: they run it as a service behind a
cache, and they publish the lag instead of hiding it.

### Supermemory — consolidation as a scheduled stage

- Pipeline at write: extraction → chunking → embedding → indexing, then a later **"dreaming" phase** that turns content into
  temporal, fact-based memories. **HNSW** index, **cached embeddings and profiles**, **frequently-accessed caching**, and a
  **layered memory** (recent/hot in a fast KV layer, deeper memories fetched on demand). Claims sub-300 ms retrieval.

**Take:** the hot layer is not only recent data, it is **frequently accessed** data (our usage-recency criterion, confirmed);
and consolidation should be a **scheduled job**, not a read-time cost.

### turbopuffer — cheap durable storage with a hot cache in front

- **Object storage as the database**, NVMe/memory cache in front, **stateless query tier**, ≤ 3 object-storage round trips per
  search. **Cached ~14 ms p50** vs **cold ~500 ms**; 200 ms p99 at 100-billion-vector scale.

**Take:** the cold/warm split is normal and should be **published**, not apologised for. It is also the strongest argument for
making our API tier **stateless** — their servers hold no state, ours hold a volume and an in-process model.

### The convergence, stated once

The four moves, and how many of the five actually exhibit each:

1. **Durable, cheap source of truth** (Postgres / object storage) — **2 of 5** (Hindsight, turbopuffer); Mem0's primary fact storage is its vector store, with SQL an ADD history log, so counting it here would be our inference rather than its documentation — we have it.
2. **A derived, hot, disposable layer** in front — **5 of 5** — we have the shape, and the eviction executor.
3. **Expensive work at write time, behind a queue** — **4 of 5** (Mem0, Zep, Hindsight, Supermemory) — we do not.
4. **Parallel retrieval legs with bounded traversal** — **2 of 5** (Hindsight's four legs; Mem0's four signals, scored in parallel
   and rank-fused — no bounded graph traversal) — we **do** dispatch in parallel and lack only the bounded-traversal half.

So: move 2 is universal, move 3 is near-universal, move 1 is **two of five** and depends on how generously "cheap store" is read, and **move 4 rests on two comparables' published designs** (Hindsight's four parallel legs, Mem0's multi-signal scorer) — worth adopting, and still the thinnest of the four moves rather than a convergence. Item 3 is Phase A work (`A1`); item 4 is `#14192`, tracked as PLAN `F1`. Both cost only code.

## 3. The roadmap — phase A is free

### Phase A — free prep (do now, no new spend)

| # | prep | why it is free | what it unlocks |
|---|---|---|---|
| **A1** | **Make the projection consume `journal_event` on a worker loop** (same box, separate process) instead of inline | the log and the fold already exist | writes ack fast; a slow projection stops being a slow request. Mem0's < 50 ms ack without new infra |
| **A2** | **An embedder seam**: one interface with three backings — in-process model, local worker, external API | a seam is code, not hardware | we can move the embedder to its own process/machine/API **by configuration**, and benchmark each |
| **A3** | **Query-embed micro-batching** with **two** bounds: a hard maximum WAIT (e.g. ≤ 50 ms collection window) **and a maximum BATCH SIZE** (small for interactive traffic — 4–8; deep queues belong to backfill) | pure code | **5.3×** throughput on the same cores (the paired bench: 1.9 s → 0.36 s per item at batch occupancy N). The window bounds **wait-to-admit**; only the batch-size cap bounds **tail latency**, and at 32 arrivals the uncapped batch is an 11.6 s wall time — worse than the 1.9 s sequential arm |
| **A4** | **Stage timings on SUCCESS**, not only in a 504 body (p50/p95 per stage, exported) | instrumentation | every later decision (A3, CPU, external API) becomes a measurement instead of an argument |
| **A5** | **Define the scaling unit** in one paragraph: *N tenants' working sets + 1 embedder + 1 record connection pool*, with the per-tenant footprint from `#14015` (M5) | a written definition + a measured number | "how many machines" stops being a guess; autoscaling later has a predicate to scale on |

### Phase B — small spend, when A is measured

- **B1. Dedicated CPU** on the API box (removes burst throttling; the 0.24 s ↔ 2.07 s spread on identical work is the signal).
- **B2. A second API instance** for survivability (a restart or a wedged process stops being an outage) — **after** `#14067`
  (snapshot invalidation, **merged**; it closes `#13123`) and with the deploy queue fixed, or it will be an availability *risk* as it was on 2026-11-20.

### Phase C — when load justifies it

- **C1. Separate the embedder into its own deployment** (CPU-sized independently), with the API never embedding inline.
- **C2. Scale API replicas on queue depth / in-flight requests**, not CPU.
- **C3. Move the keyword leg to Postgres** (`#14000`) so the graph holds only what it is uniquely good at, and the read path stops
  paying graph round-trips for text search. ⚠️ **This one is free, code-only work** — it sits under Phase C because it belongs with
  the read-path changes, not because it needs spend; the plan sequences it as **S9** in the free group, to proceed in parallel.

## 4. What we should explicitly NOT copy

- **Batching without a max-wait bound.** The literature is explicit: batching improves throughput and worsens tail latency. A
  hard, small collection window is what keeps an interactive search interactive — **and it is only half the bound**: the window
  caps how long a request waits to be admitted, while the batch's own size caps how long it can then be trapped. A3 specifies
  both, because a window alone admits a 32-item batch and hands the last arrival an 11.6 s wait.
- **"Just add replicas".** With embeddings in-process, each replica carries its own 432 MB model and its own cold start; the
  unit is more expensive than it looks. Replicate *after* A1–A3, when a replica is a thin API process.
- **Hiding the delay.** Zep *states* that new facts appear after a processing delay. We already have the vocabulary for this
  (`#2952`: a degraded read is declared); the async write path must use it rather than pretending the data is instantly
  searchable.

## 5. The one-line version

**Our topology already matches the leaders — record + derived layer + hybrid legs. What differs is that they moved the expensive,
batchable work into queues and workers, and we still do it inside the request.** Phase A is entirely free and **shrinks one
stage**: **A3** takes the embed from 2–4 s to the batch cap — at A3's own 4–8 item ceiling that is **~1.4–2.9 s** (4–8 × 0.36 s of
per-item throughput), not 0.36 s, which is a batch cost and not a request's latency — and **A1** takes the write off the request.
⚠️ **Nothing in Phase A touches `pre_handler`**: the queue wait that dominates the slow trace is Track F's job (**F0** stops the
retry storm; **S4** lands the guards), and the legs are **already concurrent** — `#14192` (**F1**) must be re-scoped to bounded
traversal and per-leg budgets before a week is spent on it, and either way the retrieval stage's 1.2 s is at most what it can buy
(§2.5). On the same slow trace, Phase A's own residual is therefore `pre_handler` 5,996 ms + **1.4–2.9 s** + 1.199 s ≈
**8.6–10.1 s** — better than 11.35 s, and still not a budget, which is what **S3** exists to write down. Only **S7/C1** takes the
embed off the request entirely; A3 keeps it inline and merely overlaps it.

**Contradiction test:** no recorded decision is reversed. `STORAGE-ARCHITECTURE.md` §2.0 (record is truth, graph is derived) *requires* A1 — the fold is
already the mechanism. `#11758` (per-account storage) and `#4495`/`#5331` (no node pricing) are untouched. A2 does not pick a
vendor; it makes the choice reversible.

---

## 6. Reconciliation with the 2026-11-21 hosted handoff

⛔ **The handoff is an external session document — it is NOT in this repo** (no `HANDOFF-2026-11-21*` file exists on any ref; the
tracked handoffs under `docs/` are from August). Its claims are therefore **restated in full in the table below**, so this section
stands alone and nothing here is checkable only against a file a reader cannot open.

Each claim was re-measured before being adopted — several were true when written and are not true now. The handoff warns about
exactly this decay in its own §5.4 (an **external** section that cannot be opened here; the warning, restated: a claim that was
true when written ages out, so re-measure before adopting it).

| handoff claim | verdict | evidence (2026-11-21) |
|---|---|---|
| **"The fix was capacity, not code"** — 2 → 4 cores took search from 504@11.4 s to 200 in 0.84–1.12 s | **direction holds, figure does NOT reproduce** | machine is **4 cores / 4096 MB** (`fly scale show`). Ten searches now, in the order taken: **four timed out at the 30 s client bound**, **one 504 at 13.5 s**, then **five 200s at 2.7 / 2.9 / 3.7 / 4.5 / 7.1 s** — i.e. **5 of 10** succeeded. Their 0.84–1.12 s was measured on a *quiet* box; the box is rarely quiet (see the next row). |
| **"Version the 4-core box — PR `#14156` is the highest priority"** | **already done** | `#14156` **MERGED** (the 2→4 step is commit `69908d006b`); `fly.toml:276` declares `cpus = 4` in `origin/main` **and** in this checkout, and `fly scale show` reports **4 cores / 4096 MB**. |
| **"908 sessions spooled; the backlog feeds itself; serialise the drain"** | **confirmed in substance, wrong in every figure** | There are **two** populations and neither is the handoff's 908. The capture **spool** (`~/.tortoise/capture-spool/entries`) holds **1,852 entries on the box that first measured it** (host not recorded in this doc; counted: 1,852 `.meta.json`, each with its `.turns.jsonl`) and is **dormant**: `status.json` says `updated_at 2026-11-17T02:25:31Z` and the directory's own mtime (`stat`) is `2026-11-17 02:25:31`, so on that box the "feeds itself" growth is **not** visible. Re-measured 2026-11-21 after an intermediate revision quoted 5,604 / 2026-11-13: that pair reproduces **exactly** on the box this review ran on (host `cmux`; all 5,604 of its `*.meta.json` carry one `machine_id`, `aaa27f40…`), as 5,604 `*.meta.json`, `status.json` `updated_at 2026-11-13T03:21:21Z` and a directory mtime that agrees. The mtime caveat is a separate observation, not part of that reproduction: the oldest spool file's mtime is `2026-11-04` while the entries' own `created_at` reach `2026-10-29`, so the oldest spool file's mtime postdates an entry's `created_at`, and mtime is not entry age. So the two lanes measured **different machines** and neither figure is a correction of the other — the count is **per box**. A count in this section is worth the command beside it **and** the box it ran on. The day-file backlog is `~/.tortoise/session-events/*.jsonl` — count it when citing it, never carry a figure. What survives the re-measurement is the part that mattered: **the backlog is large, nothing has drained it, and the drain choice is open** (`PLAN` §8). |
| **"`#11892` is stale — the 25,000-point cap refuses every write"** | **confirmed** | `POST /v1/points` → **200** measured independently; the issue's own last comment already showed successful write-backs. |
| **"The transport wait bound is 10 s and is a recorded owner decision (`#3834`)"** | **confirmed** | consistent with the refusals we see: `transport wait bound (10s) exceeded …`. **This is why "raise the budget" is not a fix.** |
| **"`fly scale vm` does not survive a deploy"** | **confirmed by construction** | `fly.toml` is what a deploy applies; `#14156` fixed exactly that. |
| **"Contradiction is only detected for numeric/date changes; negation and noun substitution leave two rival claims with no edge"** | **confirmed, and it is a real product gap** | their classifier table (`UPDATE`/`REVISES` vs `ADD`), plus the competitor evidence in §6.1 below. |

⚠️ **The 504 is the *best* failure this box produces, not the worst.** A request that hit the 10 s transport bound and rendered a
refusal at 13.5 s is the benign case; the four that died at the **30 s client bound** are the case where the loop was blocked so
completely that no refusal could render at all. So the 10 s bound does not bound the outage — it bounds only how fast a *refusal*
arrives, and reading the four client timeouts as "also 504s, at a different number" would understate them.

### 6.1 The contradiction gap — what the competitors do, and what we should take

Our gap: a numeric change mints `CORRECTS`, but *"staging" → "production"* or *"is required" → "is not"* leaves **two claims
alive with no marker**. The demo plan already works around it (use a number), but the gap is product, not demo.

**Graphiti / Zep — bi-temporal validity, and it is the SOTA answer:**

- **Two timelines**: **valid time** (`valid_at` / `invalid_at` — when the fact is true in the world) and **ingestion time**
  (`created_at` / `expired_at` — when the system learned or retired it).
- **On a contradiction whose validity intervals overlap**: the old edge is **closed, never deleted** —
  `invalid_at = new_edge.valid_at`, `expired_at = now()`. History survives; the current answer is unambiguous.
- Facts are compared on **world-time dates, not arrival order**.
- **The division of labour is the one we already use for JEV**: an **LLM decides "duplicate or contradiction"**; a
  **deterministic rule applies the interval arithmetic**. Nobody lets the model do the bookkeeping.

**What that means for us:** we do not need a new kind or a new store — we need a **contradiction arm that closes the older
claim's interval** instead of leaving two rivals, and the classifier must return contradiction for negation/substitution (the
LLM's job), with the closure arithmetic done in code. That is a strictly smaller change than the `CORRECTS`-only path implies,
and it is the difference between "memory that accumulates" and "memory that resolves".

**Filed as `#14198`.** It also gives the demo a second, stronger variant (a noun substitution, not only a number).
