# Scaling roadmap — what comparable memory systems do, and the free prep work

**Date:** 2026-11-21 · **Status:** research + roadmap · **Owner ask:** *"research how competitors (memory systems) approach
this in their codebase … what can we learn about SOTA practices and start to build in the right direction of an
infrastructure that can handle load. We can have things ready for scaling without paying yet."*

**Search:** `web_search model=sonar` — rung 2 (rung 1 `seo-intelligence` unavailable on this box) — 3 queries attempted /
3 returned. Sources are vendor papers, engineering blogs and architecture references; they are **third-party claims, not
reproduced here**.

---

## 1. What the comparables actually do

### Mem0 — separate the write plane from the read plane

- **Topology:** a SQL **source of truth**, a **vector store** for embeddings, an **entity store** for relationship-aware
  retrieval. *That is our shape* — Postgres record + graph + vectors.
- **Writes are asynchronous**: an add returns in **< 50 ms** and the real work (fact distillation, deduplication, embedding,
  entity linking, temporal reasoning) happens on a queue. Workers autoscale on **queue depth**, and tenant ordering is kept by
  **partitioning**.
- **Reads are the latency-critical plane**: hybrid search reported at **100–150 ms**; paper figures p50 0.148 s / p95 0.200 s
  for search.
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
| system of record | **Postgres record** (§2.0 owner ruling) | Mem0: SQL source of truth ✅ same |
| derived search layer | **graph cache + pgvector** | Zep: Konig with hot-graph cache ✅ same shape |
| hybrid retrieval | **semantic + keyword + graph** | Zep/Mem0: same three legs ✅ same |
| **write path** | synchronous through the API | **queued, acked < 50 ms**, workers autoscaled ✗ ours is inline |
| **query embedding** | **inside the request**, 2–4 s on the box's shared cores (4 since `#14156`) | dedicated worker tier / external API ✗ |
| **batching** | none for interactive queries | continuous batching + max-wait bound ✗ |
| eviction / working set | executor + usage signal merged (`#10976`, `#14011`) | hot-graph cache ✅ we have the parts |
| admission control | wait budgets + refusal accounting | latency budgets + admission control ✅ we have the shape |
| **stage metrics** | present on **error** paths only (`stages_ms` in a 504 body) | per-stage p50/p95 **always** ✗ |
| scaling unit | one process, one machine | queue depth → replicas ✗ |

Two structural gaps, everything else is already aligned or landed:

1. **The read path does the expensive, batchable work synchronously.** A single query embed costs ~2–4 s here; batched it is
   ~0.36 s per item — a ~5–10× difference that is pure scheduling, not hardware.
2. **The write path is inline.** We already *have* the queue in embryo: `journal_event` is an append-only log and the
   projection is a fold over it. What is missing is a **worker that consumes it on its own schedule**.

---

## 2.5 Five comparables, in detail — and what to take from each

### Hindsight (Vectorize) — the closest match to us, and the most useful

- **Storage:** **PostgreSQL + pgvector**. Not a bespoke vector engine — the same stack we chose in §12.1c.
- **Retrieval:** *"Recall combines **vector, keyword, graph, and temporal** search **in parallel**"* — we run three of those
  four legs, but **sequentially**.
- **The sentence that should reorganise our roadmap:** *"The design shifts **LLM cost to writes** … Embeddings, fact
  extraction, and graph structures are **prepared during retention to reduce retrieval-time work**."* We do the exact
  opposite: the query's embedding is computed **inside** the read.
- **Latency is set by the slowest branch, not the sum** — *"Recall latency is determined by the slowest parallel branch
  rather than the sum of all branches."* Our stages are a chain (`pre_handler → embed_encode → retrieval → ep → ordering`), so
  we pay **the sum**; that is why we measure 6 s where a parallel design measures 600 ms.
- **Bounded graph traversal** and **hierarchical compression** (facts → observations → mental models) — the compression ladder
  is shape-identical to our Point → Object/Event → goal map.
- **Memlake:** durable data lives in **object storage**, and **servers are disposable caches**.
- **Published numbers:** recall **100–600 ms**, reflect 800–3,000 ms, retain 500–2,000 ms per batch.

**Take:** parallelise the legs; move cost to writes; bound traversal; keep servers disposable. Their published recall budget
(100–600 ms) is the number we should be able to defend.

### Mem0 — the write plane as a product

- SQL source of truth + vector store + entity store. Adds ack in **< 50 ms**; extraction/embedding/dedup/entity-linking run on a
  **queue**, workers autoscaled on **queue depth**, tenant ordering by **partitioning**. Search **100–150 ms**
  (p50 0.148 s, p95 0.200 s).

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

1. **Durable, cheap source of truth** (Postgres / object storage) — **3 of 5** (Mem0, Hindsight, turbopuffer) — we have it.
2. **A derived, hot, disposable layer** in front — **5 of 5** — we have the shape, and the eviction executor.
3. **Expensive work at write time, behind a queue** — **4 of 5** (Mem0, Zep, Hindsight, Supermemory) — we do not.
4. **Parallel retrieval legs with bounded traversal** — **1 of 5** (Hindsight alone) — we do not (we chain them).

So: move 2 is universal, move 3 is near-universal, move 1 is a majority, and **move 4 rests on a single competitor's published design** — worth adopting, and worth labelling as one source rather than a convergence. Item 3 is Phase A work (`A1`); item 4 is `#14192`, tracked as PLAN `F1`. Both cost only code.

## 3. The roadmap — phase A is free

### Phase A — free prep (do now, no new spend)

| # | prep | why it is free | what it unlocks |
|---|---|---|---|
| **A1** | **Make the projection consume `journal_event` on a worker loop** (same box, separate process) instead of inline | the log and the fold already exist | writes ack fast; a slow projection stops being a slow request. Mem0's < 50 ms ack without new infra |
| **A2** | **An embedder seam**: one interface with three backings — in-process model, local worker, external API | a seam is code, not hardware | we can move the embedder to its own process/machine/API **by configuration**, and benchmark each |
| **A3** | **Query-embed micro-batching** with a hard maximum wait (e.g. ≤ 50 ms collection window) | pure code | ~5–10× throughput on the same cores; bounded tail latency by construction |
| **A4** | **Stage timings on SUCCESS**, not only in a 504 body (p50/p95 per stage, exported) | instrumentation | every later decision (A3, CPU, external API) becomes a measurement instead of an argument |
| **A5** | **Define the scaling unit** in one paragraph: *N tenants' working sets + 1 embedder + 1 record connection pool*, with the per-tenant footprint from `#14015` (M5) | a written definition + a measured number | "how many machines" stops being a guess; autoscaling later has a predicate to scale on |

### Phase B — small spend, when A is measured

- **B1. Dedicated CPU** on the API box (removes burst throttling; the 0.24 s ↔ 2.07 s spread on identical work is the signal).
- **B2. A second API instance** for survivability (a restart or a wedged process stops being an outage) — **after** `#13123`'s
  snapshot invalidation and with the deploy queue fixed, or it will be an availability *risk* as it was on 2026-11-20.

### Phase C — when load justifies it

- **C1. Separate the embedder into its own deployment** (CPU-sized independently), with the API never embedding inline.
- **C2. Scale API replicas on queue depth / in-flight requests**, not CPU.
- **C3. Move the keyword leg to Postgres** (`#14000`) so the graph holds only what it is uniquely good at, and the read path stops
  paying graph round-trips for text search.

## 4. What we should explicitly NOT copy

- **Batching without a max-wait bound.** The literature is explicit: batching improves throughput and worsens tail latency. A
  hard, small collection window is what keeps an interactive search interactive.
- **"Just add replicas".** With embeddings in-process, each replica carries its own 432 MB model and its own cold start; the
  unit is more expensive than it looks. Replicate *after* A1–A3, when a replica is a thin API process.
- **Hiding the delay.** Zep *states* that new facts appear after a processing delay. We already have the vocabulary for this
  (`#2952`: a degraded read is declared); the async write path must use it rather than pretending the data is instantly
  searchable.

## 5. The one-line version

**Our topology already matches the leaders — record + derived layer + hybrid legs. What differs is that they moved the expensive,
batchable work into queues and workers, and we still do it inside the request.** Phase A is entirely free, and it is the
difference between "a machine that answers in 6 s" and "a machine that answers in 300 ms and catches up in the background".

**Contradiction test:** no recorded decision is reversed. §2.0 (record is truth, graph is derived) *requires* A1 — the fold is
already the mechanism. `#11758` (per-account storage) and `#4495`/`#5331` (no node pricing) are untouched. A2 does not pick a
vendor; it makes the choice reversible.

---

## 6. Reconciliation with `HANDOFF-2026-11-21-tortoise-hosted.md`

The handoff is from another agent's session. Each claim below was re-measured before being adopted — several were true when
written and are not true now, which is exactly what its own §5.4 warns about.

| handoff claim | verdict | evidence (2026-11-21) |
|---|---|---|
| **"The fix was capacity, not code"** — 2 → 4 cores took search from 504@11.4 s to 200 in 0.84–1.12 s | **direction holds, figure does NOT reproduce** | machine is **4 cores / 4096 MB** (`fly scale show`). Ten searches now, in the order taken: **four timed out at the 30 s client bound**, **one 504 at 13.5 s**, then **five 200s at 2.7 / 2.9 / 3.7 / 4.5 / 7.1 s** — i.e. **5 of 10** succeeded. Their 0.84–1.12 s was measured on a *quiet* box; the box is rarely quiet (see the next row). |
| **"Version the 4-core box — PR `#14156` is the highest priority"** | **already done** | `#14156` **MERGED** (the 2→4 step is commit `69908d006b`); `fly.toml:276` declares `cpus = 4` in `origin/main` **and** in this checkout, and `fly scale show` reports **4 cores / 4096 MB**. |
| **"908 sessions spooled; the backlog feeds itself; serialise the drain"** | **confirmed and WORSE** | local spool is **916 entries** (was 908 in the handoff, 724 before). It grows while nothing drains. |
| **"`#11892` is stale — the 25,000-point cap refuses every write"** | **confirmed** | `POST /v1/points` → **200** measured independently; the issue's own last comment already showed successful write-backs. |
| **"The transport wait bound is 10 s and is a recorded owner decision (`#3834`)"** | **confirmed** | consistent with the refusals we see: `transport wait bound (10s) exceeded …`. **This is why "raise the budget" is not a fix.** |
| **"`fly scale vm` does not survive a deploy"** | **confirmed by construction** | `fly.toml` is what a deploy applies; `#14156` fixed exactly that. |
| **"Contradiction is only detected for numeric/date changes; negation and noun substitution leave two rival claims with no edge"** | **confirmed, and it is a real product gap** | their classifier table (`UPDATE`/`REVISES` vs `ADD`), plus the competitor evidence in §6.1 below. |

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
