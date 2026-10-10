---
title: Storage Architecture — the physical layout of the record
type: engineering
domain: platform
doc_status: draft
created: 2026-09-23
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: STORAGE-ARCHITECTURE.md, records ledger, vector index, raw storage, journal
---

# Storage Architecture — the physical layout of the record

**Status:** design — **AMENDED 2026-10-08 to the owner's hybrid ruling** (§2.0): **Postgres is the system of record for every layer; FalkorDB is a derived hot cache.** Read §2.0 and §3's table first. **Scope:** where Tortoise's data physically lives, and what that costs. This document is written in the vocabulary of `docs/ONTOLOGY.md` (v3.15) — it does not introduce new layers.

**⚠️ ONE WORD, TWO PRODUCTS — read this before any section mentioning "Supabase".** **Supabase *is* Postgres** (a managed Postgres is its core product). This document uses the single word for **three different things**, and the overload has already caused a real misreading — the owner asked *"why both Postgres and Supabase?"*, when **there is ONE database**: **(1)** the **Postgres vendor** (§14) · **(2)** the **Postgres database itself** (e.g. *"vectors move to Supabase Postgres (`pgvector`)"*, §12.1c — `pgvector` is a Postgres extension) · **(3)** **Supabase Storage**, the **object bucket** (true binaries only). **Every mention means one of these three; nothing here describes two databases.**

**⚠️ Code citations — revision pin.** Every `file.py:NNNN` reference in this document is pinned to commit **`c79ba1cf2`**. Line numbers drift as the code moves; **the symbol name is authoritative and the line number is a convenience.** Verify against the symbol, not the number.

**Related:** **`#7869`** (this amendment — the doc's canonicalisation) · `#5086` (substrate-rebuild tracking) · `#5089` (durability — derived = replay(journal)) · `#5090` (the vector layer) · `#3885` (raw storage modes) · `#3998` (the two-store question) · `#3895` (restore drill) · `#4894` (extractor v4) · `#4240` (edge durability) · `#4333` (the cost investigation — **another lane's**) · `#4614` (quota cliff) · `#4889` (Subject layer) · `#1026` (pack slots) · `#4899` (the gate). **Full issue map: §13.**

---

## ⛔ THE STORAGE UNIT IS BYTES — decision, owner, 2026-09-26 (`#4495`)

**Read this before the rest of the document.** The body below is written in the **node** unit (`~25,000 quota
nodes`, `140 MB`, `$73/GB`, per-node byte constants). The owner ruling of 2026-09-26 keeps the *bytes* and retires
the **node as the customer-facing unit**:

- **Storage is billed in MB/GB**, as both our cost input **and** the customer-facing unit — *"migrate away from
  counting nodes and start counting in mb/gb for storage (both as a cost input for us and customer facing bill them
  per mb/gb as they understand that) **[typos in the source silently normalised: `coutnign`, `inout`]**"*.
- **Exceeding the storage allowance is PURCHASED OVERAGE, not refusal** — consistent with the 22 September ruling
  that tiers price *features*, never refusal.
- **Overage is bought with PREPAID CREDITS**, not a postpaid "max spend" approval — the owner's stated reason is
  cashflow.
- The tier carries a **starter amount** of storage and usage; the subscription itself is priced on **features**.
- **Prices are NOT set by this document or by this ruling.** The owner *"calibrat[es] after the beta launch"*, from
  measured consumption.

**⚠️ Why the node unit is not merely deprecated but replaced — and what actually removes the P0.** The stored data
*"does not shrink back on its own"* (**the wording is this issue's QUESTION comment, not the ruling's** — the
ruling's own synthesis renders the same fact as *"stock never resets while flow does"*. The qualifier carries the
argument, so it is kept: a node count does fall when nodes are deleted
or purged — but **not** when one is superseded, which **does not delete the old point**: it marks it outdated and
adds a `CORRECTS` edge. The node is still there, and the quota predicate carries no status filter, so a superseded
point is still counted), so a customer who filled the stock cap stayed full **without doing anything further** — the
P0 in `#4495` was **24,978 of 25,000, i.e. 22 points of headroom**, and a later capture in the same window was
refused outright at **24,984 of 25,000 (16 points)** with no partial acceptance.

⇒ **What removes the permanent-refusal state is the PURCHASE PATH, not the unit.** A byte allowance with no overage
— or a customer who declines to buy — refuses exactly as the node cap did. The ruling therefore does **not** extend
the never-refusal ruling to the node cap; it **removes the unit the question was about**, and pairs the new unit with
a way to buy more. Keeping those two halves distinct matters: the guarantee comes from the overage path.

**Measured basis (2026-09-25, live graph).** These are the numbers the allowance must be denominated in, and **each
per-node figure states its base and its denominator**, because that pair is where this document has already erred
twice:

| quantity | measured | note |
|---|---|---|
| resident nodes | **89,701** | `MATCH (n)` |
| nodes the quota predicate counts | **24,978** | **3.59× divergence** from the resident count — the denominator trap |
| `GRAPH.MEMORY USAGE` | **143 MB** (indices 46 MB) | sampling estimate, `SAMPLES`=100 |

**Per-node figures must not be quoted without a base.** Taking the ruling's headline reading (143 MiB):

- **6,003 B per *capped* node** (143 MiB ÷ 24,978)
- **1,672 B per *resident* node** (143 MiB ÷ 89,701)

⚠️ **The ruling records `1,594 B` per resident node, and that figure is on a DIFFERENT base** — it is
143 **decimal** MB ÷ 89,701. So the ruling's two per-node numbers (`6,003` and `1,594`) differ by **3.77×**, not by
the **3.59×** the denominators alone imply, precisely because MiB and decimal MB were mixed. **On one base the pair
is either `6,003 / 1,672` (MiB) or `5,725 / 1,594` (decimal)** — at most two of `{143 MB, 6,003 B, 1,594 B}` can be
true at once. The *decision* is untouched by this (bytes remain the unit); the *derivation* is corrected here so the
error is not propagated into the allowance.

⚠️ **This block SUPERSEDES the body's older figures.** §2 and §12 read **141 MB** and **45 MB** of indices where this
block reads **143 MB / 46 MB**, and §13's issue-map row concludes *"~3 KB per node (140 MB ÷ ~45k total nodes)"* —
a **third** denominator (~45k, and a different residency set). The **`~3 KB` estimate is superseded by this block's
`1,672 B` per resident node**; the figures are not interchangeable and the `~45k` node count is not re-measured here.
§13's row carries the earlier denominator correction (the retracted 5.6 KB claim); **this block, not §1, is the
precedent for the denominator error class** — §1 corrects a *growth-horizon* error, a different mistake.

**⛔ Status of the implementation — read this as FORWARD-LOOKING, not shipped.** Nothing in the byte path is on
`main` today:

- `tortoise/graph_storage.py` is **not on `main`** (nor on this document's own revision pin `c79ba1cf2`). It exists
  only on the **unmerged** branch `feat/5331-graph-byte-meter` (PR #5696). *"Built"* is true of an open PR only.
- **There is no byte gate on `main`.** The gate — and with it the `max_storage_bytes` allowance key — is the
  **separate**, also-unmerged branch `feat/5331-node-to-byte-cap`. The key is therefore **absent on `main`** (and
  absent from the meter branch), but it is **not absent from the repository**: it is written in `tortoise/` on that
  cap branch.
- **The meter is measurement-only and FAIL-SOFT** — its own docstring is explicit: *"NOT A DIAL. Nothing here
  prices, caps, tiers, refuses or throttles. It is the instrument, not the setting (#5331 is measurement only)"*,
  and *"FAIL-SOFT … never raise"*. Do not describe the **meter** as fail-closed; the fail-closed behaviour belongs to
  the **gate**.

⇒ **The gate itself IS written — on the cap branch, not on `main`.** `_enforce_storage_allowance`
(`tortoise/quota.py` on `feat/5331-node-to-byte-cap`) raises `QuotaCheckError` when no reading is supplied, and that
branch's `pricing.py` describes the design as *"FAIL-CLOSED when no reading is supplied"*. The gate is therefore not
missing. **What is missing is the WIRING**: no production caller supplies a `storage_reading`, so the gate is inert,
and configuring an allowance today would 500 every points-gated write.

⇒ **A requirement on the not-yet-done wiring, not a shipped default:** the meter must be wired into the gate **before**
any allowance is configured. That ordering is a constraint on the work, not a statement of what exists.

> **OVERRIDES:** the node-count storage cap (`max_graph_nodes`, per-node byte constants such as 1,024 B/node) as the
> customer-facing storage unit — replaced by measured MB/GB storage with purchased overage, because per-node
> accounting (a) declares 1,024 B/node against a measured 6,003 B/node on the capped set, (b) makes the cap permanent
> while the number it counts never shrinks **on its own**, and (c) is a unit customers cannot reason about.

---

## 1. The problem

One user's graph reached **25,000 quota nodes in 3–4 active days** - **140 MB of resident memory at $73/GB/month ≈ $9.98/month for a single user, and every further GB costs another $73/month in perpetuity.** The product's constraint is a **$19 price with <$9 total cost per user**, and the graph keeps growing with tenure.

> ⚠️ **FIGURE CORRECTED 2026-09-23.** An earlier draft said *"~6 GB ≈ ~$460/month"*. The $460 implied a **6.3 GB horizon that was never stated** and mixed a growth rate with a total. **The correct statement is the RATE: at ~1 GB/month of growth, each month of tenure adds $73/month to the bill, permanently** — which is worse than a one-off figure, because it recurs. **The $10 at 140 MB and the $73-per-GB rate are the measured numbers; any longer horizon must state its own multiple.**

The cause is not that we store a lot. **It is that we pay for the *total* rather than the *working set*, and that we store far more than the working set.**

---

## 2. The mechanism — and the DECISION: **Postgres is the record, FalkorDB is the hot cache**

### 2.0 ⭐ DECISION (owner, 2026-10-08): **hybrid now — Postgres is the system of record; FalkorDB is a derived hot cache**
>
> *"ok, so we do hybrid now, data goes to postgres and it's loaded to hot (falkor); for now we keep the limit in falkor high so we don't need to rush with enabling our queries to parse both falkor and postgres."*

**This is the governing decision for this document.** Everything below is either (a) the reasoning behind it, or (b) the work that remains.

**⚠️ IT AMENDS — and in one respect SUPERSEDES — the 2026-09-24 decision** (*"for now we can keep FalkorDB hosted and then we figure out further optimisation"*). **What changed is the TRIGGER, not the destination.** The 2026-09-24 record deferred the Postgres target until *"there are real users and a measured footprint"*; **the 2026-10-08 ruling reverses that trigger, because migration cost scales with CUSTOMER COUNT** — so the cheapest window is **before the first customer**, and the deferred trigger named the most expensive one. **The destination was never in dispute:** §3 already says the derived layer is Postgres.

**✅ WHAT SURVIVES FROM 2026-09-24, UNCHANGED — and it is the whole reason this amendment is small:**

- **The graph engine stays hosted FalkorDB.** The hybrid does **not** drop it; it demotes it from *the record* to *a cache*. Self-hosting remains a live ops question for the cache tier (§12.2b lever 5), not a rescue.
- **The PROVIDER question is untouched** — the ruling moves **who is authoritative**, not which Postgres vendor (§14). Its **PLACEMENT** half is superseded, because placement and authority are now decided (§2.0 SCOPE). That separation is what keeps the change narrow.
- **§3's architecture was already the hybrid.** Its table already read *"the derived layer … Postgres; RAM-cached"*. §3's **split** does not change; this amendment adds the **authority/cache columns** (§3) that make it operational rather than aspirational.

**⚠️ SCOPE — this decides a PLACEMENT AND AN AUTHORITY, and it settles what §2 previously deferred.** The earlier note here read *"this decides a PROVIDER, not a PLACEMENT (owner clarification, 2026-10-06) … This decision does NOT rule on what belongs in the graph and what belongs in Postgres."* **That is no longer the state.** The 2026-10-08 ruling **does** rule on placement: **Postgres is the system of record and FalkorDB is its cache.** §9's placement rules are amended accordingly and explicitly (§9.1 — raw text is Postgres). **Any further departure from §9's split requires careful research and the owner's explicit approval.**

**What is true now:**

| | what it is | authority | served from |
|---|---|---|---|
| **the system of record** | **Postgres** (hosted by Supabase — the database *is* Postgres, §14) | **Postgres** | Postgres |
| **the hot cache** | **FalkorDB Cloud, hosted** | ⛔ **not authoritative** — rebuildable, never the only copy | FalkorDB |
| **raw text + the S1 narrative** | the narrative **with** its raw transcript (§9.1) | **Postgres** | Postgres |
| **artifacts** | recordings, attachments — true binaries only | object storage | object storage |
| **the cost levers** | write less noise (the extractor work) · a **chosen hot set** — we choose what the cache holds, since the vendor exposes no per-graph eviction (§2.1) · density | — | — |

**⚠️ TWO CLAUSES OF THE RULING LIE OUTSIDE THIS DOCUMENT'S SUBJECT — but they are part of the decision, so they are recorded here.** **(a) The split of labour:** **EP confidence is computed in Python**, and **BFS moves server-side**. **(b) The sequence:** **(i)** one write path, so the log is the truth → **(ii)** BFS server-side → **(iii)** *then* migrate. **The ruling's *"keep the limit in falkor high so we don't need to rush"* clause is exactly what that sequence buys:** the cache stays fully populated while (i) and (ii) land, so **no query has to read both stores yet** — and the deferral is bounded by that work, not by a date. **(i) is §17.1's subject; (ii) belongs with the connection-layer cost work in §11.**

**Why deferring the ENGINE change cost us little — and why deferring the RECORD would not (measured 2026-09-24, §16):**

- **Retrieval latency is a non-issue at our size.** The published *"50–500ms budget"* is **vendor self-report**, not a requirement — and the one published measurement (Mem0) shows retrieval at **20–25% of total turn latency** against a turn of **p50 708ms / p95 1.44s**. At 140 MB the measured equivalent is **single-digit ms**.
- **The engine is not the bottleneck; the rent is.** `$0.10/GB-hour` ≈ **$73/GB/month on PROVISIONED memory** — so the same code self-hosted on a modest machine costs a fraction, and *that* is the lever, not a rewrite.

### 2.1 What FalkorDB actually charges for — measured 2026-09-24

⚠️ **CORRECTED TWICE, AND THE TWO CORRECTIONS POINT OPPOSITE WAYS — read this before the table.** (i) An earlier draft described *"disk-resident, RAM as a cache"* as the mechanism; that was corrected on **2026-09-24**, when the graph engine was the only store. (ii) **The 2026-10-08 hybrid ruling restores the right-hand column as the RECORD and demotes the left-hand column to a CACHE.** Both columns are therefore now **live, not alternatives** — and that is exactly why the table matters: the split works *because* the two stores charge for different things.

| | FalkorDB (**the hot cache**) | Postgres (**the system of record**) |
|---|---|---|
| where data lives | **the whole graph must be in RAM** — no spill-to-disk mode is documented | on disk; RAM is a cache |
| what you pay for | **PROVISIONED instance memory**, not dataset size | the machine + disk |
| an idle tenant | ⛔ **costs exactly what an active one costs — there is no eviction** | could be parked |
| density limit | a graph may use up to **75% of instance memory** | — |
| when it runs out | writes rejected, keys evicted, or **OOM kill** | pages to disk |

**⛔ THE FINDING THAT MATTERS MOST, AND IT IS WHY THE COST IS WHAT IT IS: on hosted FalkorDB there is no such thing as an idle tenant.**

- The **only** documented removal of a graph is `GRAPH.DELETE` — *permanent, no undo*. There is **no unload**.
- Per-graph eviction **does exist** — `falkordbe.idle-threshold-ms` (LRU idle threshold), `falkordbe.evict-interval-ms`, `falkordbe.load-evict-budget`, reload via `GRAPH.LOAD` from `/data/offload`, and the offloads **survive restart** — but it is documented **only for FalkorDB Enterprise (self-managed)**. It is **not exposed on Cloud** as far as we can establish. **⇒ This is the single most valuable thing to confirm with the vendor, and it is the strongest argument for self-hosting.**

**⇒ Two consequences, and both are load-bearing:**

1. **There is no cheap-idle lever on our current stack.** Our ~140 MB user costs ~**$10/month active or idle**, and that memory is not reclaimable without deleting the graph.
2. **The only levers are density and size** — pack many graphs onto one instance (≤75% of RAM) and write less noise. **Both are ours to pull without changing engines.**

**⚠️ Also measured: `GRAPH.MEMORY USAGE` is a sampling-based ESTIMATE**, not an exact allocation — it takes `SAMPLES` (default 100, up to 10,000) and *"averages them to estimate"*. It does report a real breakdown (`indices_sz_mb`, `amortized_node_attributes_by_label_sz_mb`, `label_matrices_sz_mb`, …), and it does **not** include per-graph/Redis-key overhead. **⚠️ It is also BLIND to the HNSW vector index** (`#5331`, measured 2026-10-08 on a 20,000-node × 384-dim graph): the index lives in the separately-loaded `vectorset` module and appears in **neither** the total nor `indices_sz_mb` — measured **8.5x–21.7x** under-report on the index share and **~2x** on the total. **So 140 MB is a good number, not an exact one — quote it as an estimate, and do not wire it to a cap for a vector-bearing tenant.**

### 2.2 Why the engine is not the urgent part — and the record is

**The volume is ours to fix, and it is the biggest single lever on the bill.** Our own measurement: **45 MB of indices + 51.6 MB of embeddings + 16 MB of text**, over a graph that is **62% junk entities and ~20,000 episodic turns**. **Writing less noise reduces the RAM footprint directly on the engine we already have** — the *writing* lever needs no migration and no new ops — and it also improves search and connections (which is the actual goal).

**⇒ The order is: fix the writing, land the Postgres record, then re-measure — the engine stays as the hot cache throughout.**

**On OUR engine the rule is absolute: everything is in RAM.** FalkorDB has **no spill-to-disk mode and no quota exception** — nodes, edges, **indices and embeddings alike** are memory we rent. The `$73/GB/month` is charged on the **provisioned machine**, and a graph may occupy at most **75% of it**.

**⇒ Therefore, on FalkorDB, cost IS proportional to total data — and there is no "unread data costs nothing" escape.** ⚠️ **That is precisely the argument for demoting FalkorDB to a CACHE (2026-10-08): the escape exists in Postgres, so bytes that are never read again live there and never rent RAM.** On FalkorDB the same fact is why the **hot set must be chosen** rather than accumulated.

### The class that dominates our bill — measured

**In our graph the vector class — measured (§12.1b) — is ≈50 MB of stored vector properties plus a 45 MB index total (of which only part is the `Point` HNSW index) against a 141 MB graph, i.e. around half.** ⚠️ **An earlier draft said *"95.6 MB of the 140 MB (~68%)"* — that figure added the WHOLE index, which also contains every fulltext index we own. Read §12.1b for the measured breakdown; the honest answer is a range, not a point.**

**⇒ So "what earns an embedding" is the single biggest lever on our memory bill, on the engine we have, today.** That is §12.2's subject, and it is not a Postgres question.

**⚠️ A note on the Postgres side, kept separate so it does not confuse the current one.** Earlier drafts asserted *"pgvector's index is RAM-mandatory"* and then over-corrected it. The settled fact: **pgvector's own README says the index need not fit in memory** — *"No, but like other index types, you'll likely see better performance if they do"* — it is an ordinary Postgres index, paged through the buffer manager, and **a cold index is slower, never wrong**. **This now matters to the LIVE store, not a deferred one:** the index is pageable, so the RAM cliff moves. **It does not change FalkorDB's bill at all.** Recorded here so a future lane does not re-litigate it.

---

## 3. The architecture: the ontology's own split, made physical

The ontology already states this split. `docs/ONTOLOGY.md` §2 (state-centric model):

> *"the **Episodic layer's events ARE the truth**; status is a fold cache over the events — **never the truth itself**"*

and §4.2:

> *"`Object.status` is a **write-through cache** of lifecycle events … the **journal/event stream is the reconstruction source**"*

**We are not inventing a new split. We are making the existing one physical — the truth on disk, the derived layer where it is fast.**

| layer | what it holds | ontology types | **authority (system of record)** | **cache (served hot)** |
|---|---|---|---|---|
| **The truth** | provenance, the episodic timeline, and the graph's own change journal | `Source`, `Event`, `GraphEvent` (a document is a `:Source`, §4.4) | **Postgres, on disk** | — |
| **The derived layer** | the Semantic, Epistemic and Procedural layers — all of it a fold over the truth | **`Subject`, `Object` (+`.status`), `Point`, operators, edges** — **EP scalars recomputed, never stored as authority (§17.5)** | **Postgres, on disk** | **FalkorDB — a hot subgraph only** |
| **Artifacts** | large binary objects (recordings, attachments) | referenced by a `Source` | **object storage** | — |
| **Raw text + the S1 narrative** | the narrative **with** its raw transcript (§9.1) | referenced by a `Source` | **Postgres** | — |

**⛔ READ THE AUTHORITY COLUMN AS AUTHORITY, NOT AS LOCATION — this is the single most misread thing in this document.** **Postgres is the system of record for EVERY layer, including the epistemic layer (`Point`, operators, `IMPL`/`NAND`, edges) and the entity layer (`Object`, `Subject`).** ⚠️ **The ONE exception is the EP SCALARS — `ep_alpha`, `ep_beta`, `confidence`, `posterior_*`, `baseline_*`: they are recomputed from the structure and never stored as authority** (the ontology: confidence *"is derived at read time … **never stored independently**"*; §17.5). **FalkorDB is a DERIVED HOT CACHE and is never the authority: a hot entity or a hot claim may live there, and deleting FalkorDB loses nothing recoverable from Postgres.** The correct sentence is *"authoritative in Postgres, cached in FalkorDB"* — **never** *"they left FalkorDB"*. **What the cache holds is exactly §9.3's list** (*"entities · Points · operators · connections"*) — **that is a description of the CACHE, not of the home.**

**⚠️ Naming caution — two different things are both called "event":**

- **`Event`** — an *ontology* type: a domain occurrence ("what happened when"). Episodic layer.
- **`GraphEvent`** — the graph's **own mutation journal** (34,020 exist). Not a domain occurrence.

### Why these four are truth — the test is PER-WRITE, not per-class

**⚠️ CORRECTED 2026-09-23 after review. An earlier draft stated the test as *"append-only — rows are added, never updated"*. That test is FALSE for two of the four types it places in the truth layer**, and it put one class in both layers:

- `Source.updatedAt` is **set ON MATCH** by `_upsert_source`; `Source.reliability` is a **derived** query-time cache written onto the node (`#398`, `sdk.py:20667` `_write_reliability_cache`).
- `Document.updatedAt` is set, and `doc_status` transitions **`captured`→`extracted`**. **⚠️ `doc_status` is RETIRED under D10/v3.15 (Q3)** — liveness is a READ, not a stored field, and this flip is **one of only two unjournalled raw-`SET` writes in the system** (`ingest.py:262-266`). The statement above describes the code **as it stands today**, not the target.
- **`Document ⊂ Object`** (`ONTOLOGY.md` §1/§4.4/§6 — `objectKind: document`), so the class appeared in **both** layers. **⚠️ SUPERSEDED (v3.15/D10): a document is a `:Source`, not an Object subclass** — see §9.3/§9.5. This bullet records the state that motivated the change.

**✅ The test that DOES hold, and it binds at the WRITE/FIELD level rather than the class or table level:**
> **TRUTH = a derivation root — nothing else can reconstruct it. DERIVED = reconstructible by replaying the journal.**

The mechanical form of the same test: **is this write carried by the journal?** (`Source`/`Document`/`Event` are **not** in `_GRAPH_EVENT_TYPES` → primary; `Point`/`Object`/operators **are** journaled → derived.)

| | **The truth** | **The derived layer** |
|---|---|---|
| **write pattern** | **append-only** — rows are added, **never updated** | **updated in place** — revised, superseded, promoted, status-flipped |
| **read pattern** | written once, read rarely (rebuild, provenance lookup) | **read constantly** — it is what every query hits |
| **what it costs** | stays **cold on disk** — no churn, cheap per GB | is the **working set** — this is what earns the indexes and the RAM cache |
| **role** | **the rebuild source** | **droppable and regenerable** |

#### ✅ A derived cache on a truth row is NOT a violation — it is a named pattern

**This dissolves the `Document` problem rather than deferring it.** A read-model cache written through to an aggregate row is the **standard CQRS pattern**, and it is safe under five rules:

1. it is a **deterministic function of truth that is carried**;
2. it is written in the **same transaction**;
3. it is **declared** as a cache;
4. it is **never the only home**;
5. it is **recomputed on rebuild, never restored**.

**`Source.reliability` already satisfies all five** — `ONTOLOGY.md` calls it a *"documented cache, never authoritative"* (`#398`). **`Document` needs placing explicitly**: its **existence is truth** (a connector discovered it); its `doc_status` is a **mutable field on a truth row** — and ⚠️ its `captured`→`extracted` flip is currently an **unjournalled raw Cypher write** (`ingest.py:262-266`, `hosted_api.py:10929`), which is a **journal-completeness gap**, filed alongside `#4240`.

#### ⚠️ The truth set is larger than these four — and the invariant is false today

**The truth set is these four types ∪ the preserved config classes** (`_config_classes()`; see `#4641`, `#4653`). So the invariant is:

```text
derived  =  replay(journal)  ∪  preserved_config
```

**⚠️ And `derived = replay(journal)` is FALSE in the code as it stands** (`#4641`, `#4653`). **Journal completeness is a PRECONDITION of this design, not an observation about it** — raw-Cypher writes (`#4240`, the `doc_status` flip above) bypass the journal today.

#### ✅ THE `#2881` COLLISION IS CLOSED — by SCOPING, not by overriding (see §14.1 **O1**)

`docs/durability-posture.md` (**`#2881`**) is a **recorded decision**: the journal *"is **never** the durability mechanism … authority stays in a store-managed artifact."* An earlier draft of this section flagged the sentence *"the journal, which is truth"* below as **colliding with it and OPEN**.

**⚠️ IT IS NO LONGER OPEN.** §14.1 **O1** (owner, 2026-09-24) settled it, and the resolution is that **the two questions are different and both hold**: **DURABILITY = the store's backup (`#2881`, unchanged); REBUILDABILITY = the journal.** **No reopen is required** — the wording is **scoped, not overridden**: *"the journal is the derived layer's rebuild source; it is not a durability mechanism."* **`#2826` A2 needs no answer.**

**⚠️ What O1 DID leave open, and it is a real gap this document must name:** O1 identifies the defect in its own words — *"`rebuild_all` performs an unconditional wipe + journal-only replay — i.e. **the code already treats an incomplete log as the authority**. That is a defect, and it gets its own issue."* **That issue is `#5089`** (*"epic: durability — derived = replay(journal) made true"*). It is the write-path half of §3's own divergence list below, whose mechanism (1) *"ONE ATOMIC WRITE"* is recorded **absent**.

**⚠️ A second finding worth its own issue: the durability gate `tests/test_durability_posture.py` does NOT scan `docs/architecture/`** — so the colliding wording passes CI today.

**Three storage consequences — and the third is what forces the split:**

1. **Different write patterns need different physical design.** An append-only table has no update churn — cheap indexes, natural clustering by time, almost no vacuum pressure. A mutable table has all three. **Put them in one table and both get worse.**
2. **The cache is per-table, and RAM is finite.** The layer that gets queried earns the cache; the layer that is written and archived does not. **Putting `Point`s and `Object`s in a cold append-only table would mean paying a disk read on every query.**
3. **⛔ The derived layer must be physically DROPPABLE.** You have to be able to drop the derived tables and regenerate them **without touching the truth**. *That is only possible if they are separate tables* — and it is exactly what `#3895` needs.

**Why `Subject` · `Object` · `Point` land on the mutable side — the mechanical reason:**

- A `Point` is **revised** (`PointRevised`), **superseded**, **promoted**, **retracted**.
- An `Object` is **mutated**, and its **status folded**.

**Those are in-place updates — and anything updated in place cannot live in a layer defined by never being updated.**

**⚠️ The consequence, and it is the storage reason this split is load-bearing: if `Point`s were truth, you could never re-extract.** v2 → v3 → v4 is exactly this — **every extractor improvement is a re-reading**. Were `Point`s the record, improving the reader would mean **rewriting rows the truth layer is defined never to rewrite**, and you would lose the ability to compare one reading against another. **The mutability of the derived layer is what makes re-extraction possible at all.**

**✅ One epistemic line, kept because it explains WHY they are mutable — the only non-storage sentence left here:**
> **A `Point` is our *reading* of the source, so it is by design CORRECTABLE — and anything correctable cannot live in the layer that is never corrected.**

**⚠️ `Object.status` is the one case that mixes both, and it is worth seeing why it is harmless.** `status` is a **mechanical fold over lifecycle `Event`s — which are truth**. So the *field* is reproducible; the *row* is mutable. **A true fold landing in a mutable table is fine: it is simply recomputable in place.**

### The invariant — and it is a REQUIREMENT ON THE JOURNAL'S SCHEMA

```text
derived tables  =  replay(the journal)      NOT  recompute(the sources)
```

**Writes go to the truth first, then project.** The derived tables are always rebuildable and therefore never the only copy.

**⚠️ This is a storage requirement, and it is precise: journal the NON-RECONSTRUCTIBLE DECISION OUTPUT** — and recompute only what is a **pure function of journalled material with no external dependency.**

The general rule (research, 2026-09-23):
> **Journal the non-reconstructible decision output, and any value whose derivation depends on an external / mutable / withdrawable artifact. Recompute only pure functions of journalled material.**

**Applied to our three cases — this is a three-way split, not one rule:**

| field | treatment | why |
|---|---|---|
| `Point.content`, kind, links, timestamps | **JOURNAL** | the model's decision output — not reconstructible |
| `content_hash` | **RECOMPUTE** | pure, self-contained. *The code already does this correctly.* |
| **`embedding`** | **⭐ JOURNAL IT** | a model that is **mutable, remote, and can be withdrawn**. Two independent literatures converge: *"the past cannot be recomputed."* |

**⚠️ `projection/__init__.py:3342` STRIPS the embedding from the journal-derived snapshot with the comment *"the replay re-derives it"*. That is the defect.** A re-embed is a **re-run, not a replay** — so as things stand the invariant is **false for that field**, and a replay after a model-revision change produces a **different graph**. Store `embedding` + `model_id` + `model_revision` + text-hash in the payload.

**⚠️ And bound the growth, which the cost model currently omits.** Naively, a Point revised N times produces **N full-payload events** and the journal grows without bound. The standard fix: **creation = full snapshot, mutation = DELTA** (the code already does this for entities — `EntityMutated.state`), plus snapshotting/compaction and retention tiering. **The journal is absent from §5/§6 entirely and must be added.**

#### Divergence detection and repair — and the answer is simpler than the doc implies

**The doc has no failure path. The research found the standard answer, in priority order:**

1. **⭐ ONE ATOMIC WRITE** — journal + projection in the **same transaction** (or an outbox with an idempotent projection). **This is the whole answer to *"the append succeeds and the projection fails"*** — the hazard cannot arise. **The Postgres target's own bullet in *Why this shape* — *"One transaction covers the journal and its projection"* — IS this fix; the doc states the benefit without naming it as the divergence mechanism.**
2. **A per-projection watermark** (`last_applied_seq`).
3. **Replay-diff** — canonical JSON → hash → compare a `projection_hash_sha256` (ESAA §3.3/App D).
4. **Guarded repair** — full wipe + replay only on unambiguous loss.
5. **Read-your-writes** while a projection lags.

**Repo status measured: (1) absent · (2) absent · (3) count-only (`consistency.py`) · (4) present and guarded · (5) absent.** ⚠️ **The current JSONL cannot do (1)** — `durability-posture.md` records it as *"wall outside the store's transaction … dual-write hazard"*. **This is a further reason the Postgres target matters, and it should be stated as such.**

> **Why `recompute(the sources)` is impossible: the extractor is not deterministic.** Re-run it over the same `Source` and you get **different `Point`s**. So the derived tables are **not a function of the sources** — they are a function of the sources **plus the model, prompt, pack and run**.
>
> **⇒ The rebuild path is REPLAY, never re-extraction.** The only thing that makes the derived tables regenerable is that the journal **stores the resulting rows**. A journal entry saying *"a point was created"* with just an id regenerates nothing.
>
> **And the payoff is identity, not just recoverability:** replay reproduces the graph **exactly**, so a v3 rebuild can be **diffed against** a v2 graph. A recompute-everything rebuild would silently produce a *different* graph each time — no stable baseline, and no reproducible audit.

**✅ The schema test this gives you, for any type or field:**
> **If the journal does not carry it, it is not derived — it is PRIMARY, and belongs in a truth-layer table.**

**✅ And "derived" does NOT mean "not saved".** `Point`s, `Object`s and `Subject`s are **stored** — in the derived tables, durably, on disk. **They are also recorded in the journal, which is truth.** The distinction between the layers is **which one is authoritative — not which one is persisted.** ⚠️ **The bullet *"the derived layer becomes REBUILDABLE"* in *Why this shape* below must never be read as *"safe to lose"*** — if the derived tables ever existed without the journal, they would be the **only** copy, the invariant above would be false, and `#3895` would be **moved rather than fixed**.

### The three movements

- **Write:** change → **truth** (append) → project → **derived layer**
- **Read:** query → derived layer
- **Recover:** derived layer ← fold(truth)

### Why this shape

- **Disk pricing** for everything that is not actively queried (~580× per GB vs FalkorDB Cloud).
- **The derived layer becomes REBUILDABLE** — which removes the current blocker: `#3895` (the restore re-drill that **failed**, 9,687/10,000 edges) exists because *the graph is currently the only durable record*. **⚠️ Rebuildable, not disposable — see the invariant above: the journal is what makes it rebuildable, and without the journal the derived layer is the only copy.**
- **One transaction covers the journal and its projection** — no dual write between them. ⚠️ **That is the truth↔derived write, and it is the only sync the pre-hybrid shape needed. The hybrid adds a second surface — Postgres (truth) ↔ FalkorDB (cache) — which §17.2 shows the document does not yet cover.**
- It is consistent with the field: GraphRAG-class systems treat the graph as a **derived, rebuildable index**, not the record (Microsoft GraphRAG persists to Parquet; only Cognee and Neo4j physically separate the two).

### What is already in the code

The store sits behind a **two-method Protocol** — `apply(event)` and `rebuild(log)` — and an **`InMemoryProjection`** already exists as a second implementation. The architecture is **event-sourced by construction**; the hosted write path does not journal unless `TORTOISE_EVENT_LOG_BASE_DIR` is set (`#4240` wired the per-graph journal) — a **wiring gap, not a design gap**.

---

## 4. Tenancy — DECIDED (owner, 2026-09-23): one database, tenant-scoped rows

**Decision: a shared tenancy — one Postgres database, one schema, rows scoped by tenant via row-level security.** Not one database (or project) per team.

**Why — the shape is already ours.** This is the same shape as the current FalkorDB arrangement — *one account, many graphs* — so it is a migration of mechanism, not of model.

> ⚠️ **The trap that makes this a real decision, not a default.** On Supabase **the platform**, *"one database per team"* does not mean one database — it means **one Supabase project per team**, and each project carries **its own compute charge ($10–60/month) before any data is stored**. Per-team projects would destroy the unit economics at exactly the scale we are trying to reach. **One project, tenant-scoped rows, is what keeps 100+ tenants on one compute bill.**

**Consequence for the derived layer.** EP confidence, `Object.status`, and the operator graph are all **tenant-scoped values**, not global ones. Nothing in the derived layer may assume a single tenant.

**⭐ AND ITS CONSEQUENCE FOR THE VECTOR INDEX — corrected 2026-09-23.** *"Rows scoped by tenant"* and *"the index is partitioned by tenant"* are **different physical designs**, and only the second bounds the working set:

- **With RLS row-scoping alone there is ONE index over ALL tenants**, and the tenant predicate arrives as a **policy-injected value — not a plan-time constant** — so the planner cannot match it to a per-tenant partial index and **will not prune**. The working set stays *all* data.
- **The mechanism that makes pruning possible is declarative partitioning (`PARTITION BY` tenant) + partition pruning**, or binding the tenant key as a **literal on the connection**. **Verify with `EXPLAIN`** — and read this as a *mechanism* note, not an endorsement of partitioning: the index-shape decision is (C), below. ✅ **MEASURED 2026-10-08 (§12.1a): `PARTITION BY tenant` stays FLAT to T=500 — 6.362 → 1.789 → 6.863 ms at T=10/100/500 (96.603 ms at T=2,100, unexplained).** ⚠️ **The pruning result — that pruning named exactly 1 partition under both an explicit predicate and an RLS-only policy — is from this lane's own measurement, and is NOT in §12.1a, which reports planning time only.** ⚠️ **But the DECISION is neither partitioning nor a shared filtered index — it is the size-earned conditional index (§12.1a: 7 indexes at 2,100 tenants, 0.71 ms there).** *(Earlier drafts of this line said "no measurement covers this yet" and cited M1; both are superseded.)*
- ⚠️ **Bypass rule, to state explicitly:** RLS is bypassed by the **table owner** and by **`service_role`**. Say which role the app connects as, and why.

**⇒ The TENANCY decision above does not change. What this bullet settles is the INDEX SHAPE, and it is THE SIZE-EARNED CONDITIONAL INDEX (C, §12.1a) — bounded relation count, earned by volume.** Per-tenant partitioning is a **measured, available alternative** (the declarative form is flat to T=500; the partial-index form degrades), **not the decision**. ⚠️ **An earlier draft of this line said the decision required partitioning and that lever 1's *"pure win"* claim depended on it — both wrong: the *"pure win"* label belonged to lever 4, and it was the PARTIAL-INDEX form that failed.**

---

## 5. Cost expectations (published rates) — ⚠️ READ WITH §6, THIS IS ONE LINE OF THE BILL

| users | data | tier | monthly | per user |
|---|---|---|---|---|
| 100 | 100 GB | Medium ($60) + disk | ~$72 | **$0.72** |
| 1,000 | 1 TB | 2XL ($410) + disk | ~$534 | **$0.53** |
| 10,000 | 10 TB | multiple | ~$4,100 | **$0.41** |

Supabase database storage **$0.125/GB/month**; object storage **$0.0213/GB/month**. Against a $9/user budget this leaves roughly **10× headroom at 1,000 users**.

⚠️ Compute tier and max-database size are coupled (a 1 TB database wants the $410 tier) and **effective IOPS is the lower of compute-supported and disk-provisioned** — a small tier caps throughput regardless of disk purchased.

### 5.1 ⚠️ Corrections to earlier drafts of this section — and the two lines it omitted

**⚠️ These figures were quoted elsewhere in an earlier draft at amounts up to ~14× higher, and that draft was never reconciled.** The numbers in the table above are the ones to use; **the ~$460 and ~$4,100-adjacent figures earlier in the doc came from a different, superseded arithmetic.**

**Two real cost lines are MISSING from the table, and both grow with the design:**

| line | why it is missing and why it matters |
|---|---|
| **the journal** | §3 requires the journal to carry the **full derived payload** — creations as snapshots, mutations as deltas. **It grows at or near the rate of the derived layer**, and it is **not in this table at all.** ⚠️ **No measurement of the journal's growth exists yet (§15 lists none).** |
| **egress + IOPS** | a cold, disk-resident design **reads from disk on every cache miss.** The cost moves from *storage* to *requests* — and egress is the line that punishes a read-heavy pattern. **Not modelled.** ⚠️ **No measurement covers egress or IOPS yet (§15 lists none).** |

**⇒ The table answers *"what does the DATA cost?"*. It does not answer *"what does the SYSTEM cost?"* — and §6 is the section that says so. Do not quote this table as a total.**

---

## 6. The split does NOT fix the volume problem

**This is the most important limitation of this document.**

⚠️ **CORRECTED 2026-09-23 — this section's multiplier was derived from a wrong vector share, and the correction moves it in the *opposite* direction from the other fixes.** An earlier draft said **~15× blended** (580× on non-vector bytes, ~5× on the vector leg), derived from §2's wrong *"vectors are ~1/3"* figure. Two things change it:

1. **The vector class is roughly half the graph, not ~1/3** (§2/§12.1b correction) — so weighting the cheap leg less would *lower* the blend.
2. **But the vector leg is no longer a ~5× RAM win.** §12.1's correction establishes the index is **disk-backed like everything else** — so **the ~584× disk-vs-RAM multiplier applies to ALL bytes, including vectors.**

**⇒ The storage-cost multiplier is larger than published, not smaller — on the order of the full ~580× rather than a 15× blend.** ⚠️ **But this is a STORAGE-cost multiplier only, and it is not the whole bill.** The real cost is **disk + compute (sized for the hot working set) + IOPS**, and **volume drives all three** — more data means a bigger working set, more scanning, and more pages to keep hot. **A storage-cost win is not a total-cost win.**

**The 100× therefore still requires two independent levers:**

| lever | magnitude | owner |
|---|---|---|
| **disk-resident storage** (this document) | **~580× on the storage line** — ⚠️ the blended *total-cost* figure is **uncomputed**; it needs the compute and IOPS lines, and **neither has a measurement yet** (M2 is *needed*, not done — §15) | `#4333` |
| **writing less** — the selection gate | **~10×** ⚠️ see §6.1 | `#4894` / extraction |

**Neither alone reaches 100×, and the two multiply.** A storage migration presented as a 100× fix would still be comparing one cost line to another.

### 6.1 ⚠️ The "~10×" is a target, not a measurement — and volume alone is a gameable metric

The extraction document states no target and no aggregate reduction; the number lives outside it. `#4899` records *"Target (owner: 'should have taken 10× longer') ≈ 2.7 per session"* **and, in the same issue, *"Realistic Phase-1 yield ≈ 48% of quota … i.e. ~2×, not 10×"*.** And `#4917` §1.9 records that the ~10× comes from a **per-item conjunction** (`save ≥ 0.5` **AND** `altitude = architecture`) measured on **n = 28, 3 kept = 9.3×**.

**⇒ Two consequences the storage plan depends on:**

- **The mechanical half is ~2×, not 10×.** The headline depends entirely on the salience gate.
- **⚠️ Volume with no retention measurement is gameable** — you can always hit a node target by writing nothing. **A volume objective must be paired with the measurement of how many durable claims we kept**, or the metric is meaningless. ✅ **The measurement is now listed (§15 M6): the owner re-affirmed on 2026-09-30 that it is wanted (§14.1 O2, AMENDED) — only the GOAL is deferred.** ⚠️ **Its denominator is a measurement-design question and is not yet specified:** a sample drawn only from the *rejects* estimates how often the rule wrongly drops something, **not** how much was kept, so M6 states the denominator requirement rather than asserting a floor.

---

## 7. State values — RETAIN is decided and landed; PLACEMENT is DECIDED (2026-10-06, below)

> **🔗 Read together with `EXTRACTOR-V4-ARCHITECTURE.md` §2.4 — the proposed fourth layer (verbatim raw facts).** Both are the same instinct — *keep the exact thing, outside the graph, linked to it* — and §2.4 carries a controlled ablation (**verbatim beats derived by 15.9 / 22.0 pts**) plus the pre-registered experiment (`#3011`) that would settle it on our stack.

**⚠️ STATUS — and the two documents disagreed until 2026-09-24** (extractor §9 records D8 as `DECIDED (retain half landed)`; this section is headed *PLACEMENT is DECIDED* as of 2026-10-06, and was headed *open* until then). **Both are right about different halves, and the split is the resolution:**

- ✅ **RETAINING verbatim operational values is DECIDED and LANDED** — commit `4a690d0be` / PR `#2456` (2026-09-07): `STATE_VALUE_CARVE_OUT` (`extractor_v2.py:145-171`) plus `VALUE_FIDELITY_RULE` (`:230`, rendered into the S2/S4 `{anti_routine}` slot and appended to S1). **The rule reaches the model.**
- ⛔ **What is MISSING is mechanical enforcement** — the rule was prompt-only; `valueGate` now EXISTS in code (`tortoise/value_gate.py`). **`#4899` is CLOSED** — whether the gate is enabled by default is not asserted here.
- ✅ **PLACEMENT is NO LONGER OPEN — it is DECIDED (owner, 2026-10-06; the decision paragraph follows immediately below).** It was: whether a state value becomes a `Point`, or a **structural field on an entity**. The paragraphs AFTER the decision record the reasoning that was open when they were written.

**✅ PLACEMENT ACCEPTED (owner, 2026-10-06): a state/numeric value is a STRUCTURAL FIELD ON AN ENTITY, not a `Point`.** The reasoning is the ontology's own: operators connect only epistemic targets, EP confidence propagates over atomic beliefs, and relevance modulation needs beliefs separable (§8) — **a belief can be argued with, an attribute cannot.** The grounded requirement (*"75% of tranche 1 spent → tranche 2 unlocks"*) is a **comparison of an attribute**, so an amount is an entity field: no confidence, **no NAND-ability, and no embedding**. **⚠️ COST — CORRECTED 2026-10-06. An earlier version of this line read *"the cost lever is therefore skipping the vector, not leaving the graph"*, and that OVERSTATES it.** The measured model (§2.1, §12.1b): **on FalkorDB the WHOLE graph is RAM — no spill-to-disk, and no eviction on Cloud *as far as we can establish*; §2.1 names confirming that with the vendor as the single most valuable thing to confirm — so cost is proportional to total data and there is NO cold-data tier.** The three measured classes this cost model turns on, on a ~141 MB graph, are **45 MB of indices + 51.6 MB of embeddings + 16 MB of text — ≈113 MB of the ~141 MB** (a PARTIAL accounting, not the whole graph: the remainder is label matrices, edges and events, and the three do not sum to the total) — ⚠️ **the 141 MB / 45 MB pair is the one §1's ruling block declares superseded (it reads 143 MB / 46 MB; see its *"This block SUPERSEDES the body's older figures"*).** The vector class is the *biggest single* lever, but **the 45 MB of indices MIXES node- and text-driven fulltext indices with the embedding-driven `Point` HNSW index (§12.1b — exactly ONE vector index in the graph, on `Point`), so node count is an INDEPENDENT lever: reifying each value into its own node adds node- and label-matrix index entries that have nothing to do with embeddings** — and an index on the value itself is RAM rented forever, proportional to how many entities carry it, read or not. **⇒ Skipping the embedding makes a value CHEAPER, not FREE** — a value is cheap only if it is **neither embedded NOR indexed**, and the moment the requirement is *sum the amounts* or *filter by amount* that is an index and recurring RAM. **This is a COST question, separate from the PLACEMENT decision above, which stands on semantics** — and it is why the disk-tier option stays live (§2.2: *"the escape exists in Postgres"*). ⚠️ **Owner ruling, 2026-10-06: a zero `Object` count on the graph inspected is CONTEXTUAL, not a finding** — other graphs hold `Object`s, so such a count says nothing about the placement, and **no live-graph count is asserted here.** **An earlier draft of this line cited a zero `Object` count on this graph as evidence that the entity tier was empty — that reading is WITHDRAWN, and the figure is dropped as evidence.** The dated measurements this cost model rests on are §12.1b's and §12.2's, each against the snapshot recorded there — **none of them states or implies that an entity tier is empty or unpopulated.** ⚠️ **A NAMING conflict is open here, and it is NOT evidence about the placement:** this document calls the graph it measures *the live graph*, *the production graph* and *a dogfood instance* interchangeably, and §12.1b's **7,859 `:Object` embeddings** (2026-09-24) cannot be reconciled with a zero count until that naming is settled. **Recorded as an open item so that a later lane settles it by measurement rather than by assuming which graph a figure describes.** ⚠️ **Where a sentence ASSERTS a figure** ("I think we spent 2 million") that assertion is a belief and may be a `Point` — the separate, smaller prose question, not this placement.

**Raised by the owner 2026-09-23:** *"State values we might need to think through how to store them for low-cost (so ideally out of RAM or only if must) yet good search and good association."*

State values are personal/entity attribute values that must survive verbatim — e.g. *"personal best 5K time = 27:12, as of 2026-08-20"*.

⚠️ **CITATION CORRECTED 2026-09-23.** An earlier draft cited *"`#1509` §9"* for the decision that state values are **Points**, and *"`#1509` E2"* for verbatim preservation. **Neither exists.** `#1509` has **no §9 and no E2**. The **superseded placement recommendation** is a **comment** on `#1509` (*"Decision requested (2.2) … A (recommended): state-value facts as Points"*) — ⚠️ **SUPERSEDED by the PLACEMENT ACCEPTED decision above (2026-10-06): a state value is a structural field on an entity, not a `Point`;** the citation correction stands unchanged — and **E2 is `#1534`'s slot** (`#1534` is now **CLOSED**; `#2453` itself writes *"STATE_VALUE_CARVE_OUT, E2/#1534"*). **Correct the same citation wherever it is repeated in the extractor doc.**

**The tension:** they need good search and good association, but they should not sit in RAM.

**Superseded proposal (this WAS the open PLACEMENT half; it is now DECIDED above — kept as reasoning, not as an open option):** state values are **structured** — subject + attribute + value + date. If they can be found **structurally** (by subject and attribute) rather than by vector similarity, they may need **no embedding at all**. **⚠️ CORRECTION 2026-10-06:** that draft then read *"the embedding is the only part that costs RAM"* — **false.** The embedding is the *largest single* part; the properties and every index are RAM too, and on FalkorDB they are RAM whether or not they are ever read. That would make them cheap to store, cheap to search, and still fully associated via `aboutSubject`/`aboutObject`.

**⚠️ STILL UNVERIFIED — and it now bears directly on the decision above:** whether the query patterns users actually need for state values are structural (find "5K time for this user") or semantic (find "things about the user's running"). **If the latter, an embedding IS required and the "no embedding" clause of the decision above does not hold** — so the decision fixes the value's PLACEMENT, while its embedding clause stays conditional on this measurement.

**⇒ This is the storage half of a two-part design.** The extractor half is `EXTRACTOR-V4-ARCHITECTURE.md` §7 — and it carries a finding that changes this section: **the existing carve-out keeps *personal* state values and actively DISCARDS decision-relevant operational ones** (`#2453`). So the table proposed here must hold **two classes**, not one:

| class | example | today | must be |
|---|---|---|---|
| **Personal state** | *"personal best 5K = 27:12"* | retained (E2 carve-out, `#1534`) | retained |
| **Operational / decision values** | *"p99 latency 480 ms at 3k rps"* | ⚠️ **RETAINED — the fix LANDED** (commit `4a690d0be`, 2026-09-07; PR `#2456`) | retained |

**⚠️ THIS ROW WAS WRONG AND THE ERROR MATTERED.** An earlier draft said operational values are *"DROPPED as a mechanics token (`#2453`)"*. **`#2453` already landed.** `STATE_VALUE_CARVE_OUT` was **extended** with an operational-value paragraph — *"a concrete value that is the SUBJECT of a decision, observation, or plan is DURABLE too … Measurements, deadlines/freezes, thresholds/TTLs, versions, and counts are carried VERBATIM"* — with the very examples the draft called dropped (*"the p95 hit 4.2 seconds"*, *"a ten minute TTL"*). It is **rendered**, not merely defined (`_granularity_text()`, `:732-744`). **What is actually missing is MECHANICAL ENFORCEMENT** — the rule was prompt-only; `valueGate` now EXISTS in code (`tortoise/value_gate.py`) and **`#4899` is CLOSED**.

**⚠️ Harmonise, do not duplicate.** `#2453` (retain operational values verbatim) · `#2817` (no numeric/locale canonicalisation exists — PT-BR/EN amounts) · `#2782` (money needs amount + currency on entities) · `#2521` (numeric aggregation across sessions) · `#2820` (tracking map — its **"state & value model"** workstream owns all of this). **Read this section together with extractor §7; neither can be built alone.**

> ⚠️ **Caveat from `#4889` — CORRECTED 2026-10-06 (owner).** An earlier version of this caveat asserted that the `aboutSubject` half **does not exist in production today** and cited **0 `aboutSubject` edges in the live graph**. **That framing is WITHDRAWN: the live graph is a DOGFOOD instance, so an edge count there was never the right test.** The code exists and is tested (`tortoise/subject_binding.py`; the `aboutSubject` write paths in `projection/edges.py` and `connectors/github.py`; `tests/test_subject_binding_1370.py`), and `#4889` is **CLOSED (COMPLETED)**. **What remains open is the end-to-end check against a real workload, which needs a capstone issue (not yet filed).** Note also that `aboutObject` is itself the largest — possibly unnecessary — edge type in the graph (§11's note).

---

## 8. Constraint carried from the extractor — granularity is NOT negotiable

**Owner, 2026-09-23:** *"The issue of more nodes costing more index space is not great but we need to have the right granularity for Points to be logical. If we have many arguments mixed together, it's not possible to NAND/IMPL nor modulate relevance."*

**This bounds how far volume reduction may go.** The Epistemic layer's operators (`IMPL`, `NAND`) act on **individual claims** — and the mitigation mechanism is the **`mitigated_by` edge on an operator Point**, graded by `mitigation_strength` (`ONTOLOGY.md` §3.9, `#2315`). ⚠️ *`MITIGATES` is **not** a registered predicate* — an earlier draft of this line listed it as one, which invites a lane to implement a type that does not exist. A Point containing several fused claims cannot be argued about:

- Operators connect only epistemic targets (`Point↔Point`, `Event↔Point`) — you cannot `NAND` a paragraph.
- EP confidence propagates over a **factor graph of atomic claims**; a fused claim gives one confidence number to several distinct beliefs.
- Relevance modulation (a mitigation reducing one claim's weight) requires the claims be separable.

**⇒ Volume reduction must come from *not writing* claims, never from *fusing* them.** The extractor's move to atomic Points (`#1509` E3) is a **requirement of the epistemic layer**, not an optimisation — and this document must not be read as licence to merge them.

---

## 9. What belongs where — two confirmed placement rules

### 9.1 The narrative lives in **Postgres** — NOT in the graph (D1, destination corrected 2026-10-09 — the owner's own answer, one day after the §2.0 ruling)

The S1 narrative (the connected prose form of a captured session) is stored as **text in Postgres, together with the raw agent-session transcript it was derived from** — **not** in the graph, **not** a `Document` node, **not** a `Point`.

**Why:** the narrative is **derived from** the source, not a document a connector discovered. Making it a graph node would (a) double the anchor count for the same input, (b) create a second thing to keep in sync, and (c) **put prose into the RAM-resident layer** — paying memory prices forever for text that is only ever *read*, never *argued about*. **The narrative is a searchable string, not a belief**: it has no confidence, is not `NAND`-able, and takes part in no operator. It is stored because it is **cheap and useful for search**, and it is given no topological weight.

⚠️ **The same rule applies to raw turns** (owner, 2026-09-23): *"the narrative is not something we're suggesting to store in the graph (same as raw) but store in supabase."* **Both of the two non-entity tiers live outside the graph.** The `Source` anchor is a **truth-layer record — Postgres-authoritative, not a cache entry (§3)** — and the heavy text sits behind it in the same store. **⚠️ "supabase" here means the DATABASE, not the object bucket — CORRECTED 2026-10-09; see the box above.**

### 9.2 A GitHub PR is an **`Object` + an `Event` + a `Source`** — never a `Source`-per-event (D2, decided)

- the **PR** → an **`Object`** (a `WorkItem`)
- its **merge** (or close) → an **`Event`**
- its **body/document** → a **`Source`**
- a **completed task** → a **lifecycle `Event`** whose **status is the fold** — **not** a second node.

**Do NOT mint a `Source` for an event.** No precedent exists for it, and **the anchor is always a document or a chunk.** ⚠️ **PROV has no `Source` class** — in PROV, *source* is a **relation**, not a type. Our class-based `Source` is a deliberate deviation; keep events out of it.

---

### 9.3 ⭐ A document is a **SOURCE**, not a graph node — and this is what makes the entity layer an abstraction

**Raised by the owner, 2026-09-24:** *"I am not suggesting making them first class, I am suggesting making them **sources** so our entity layer can be extracted from them and then we can say whether a document is outdated or not based on the entities it contains being high confidence or live (not superseded, not nanded until shown the doc is no longer reliable). That way our entity layer becomes a proper abstraction over documents, code, meeting transcripts (all sources) — that's the reasoning/knowledge layer."*

**This is not a new idea bolted on — it is what `#3919` (D30) already decided, applied consistently:**
> *"we have two storages: raw data and then the graph. **the graph indexes the files of raw data and extracts from them into our ontology.** Raw data can be hosted by us (hosted service), in the user machine, or in their own hosting preference."*

**So the shape is:**

```text
SOURCES  — documents · code files · meeting transcripts · conversations · pull requests
           all the SAME kind of thing: raw, outside the graph, cheap to store
                ↓  extracted into
FALKORDB — entities · Points (beliefs — "claims" in prose, never a type) · operators · connections
           ⚠️ THE HOT CACHE, not the home. Postgres is the system of record (§3) and this
           list describes what the CACHE HOLDS, not what it OWNS.
```

**And the consequence that makes it worth doing:**
> **"Is this document still good?" is not a stored status field — it is a READ of what we extracted from it.** Reliable entities, nothing superseded, nothing under a NAND ⇒ the document is live. A NAND on its central claim ⇒ the document is undermined. **The document inherits its health from its contents.**

**⇒ Two things fall out, and both are simplifications:**

1. **No separate document lifecycle and no `doc_status` to keep in sync.** One mechanism serves every source, and it cannot drift from the graph it describes.
2. **The reasoning layer stops being special-cased per source type.** "What do we believe about X" does not care whether X came from a spec, a repo file, or a call — **which is the whole point of calling it the knowledge layer.**

**⚠️ What this corrects in an earlier draft.** The earlier version placed `:Document` in the truth layer and then discovered it also appeared in the derived layer:
> *"**Document is an Object** (`objectKind: document`) … **Graph label is `:Document`** … the subclass relationship to Object is expressed via `objectKind: document`, **not via a second graph label. Do not create a separate `:Object` label for Documents.**"* (`ONTOLOGY.md` §4.4)

- **My "Document sits in two graph layers" finding was WRONG** — it has **one** label; the subclass is conceptual, expressed as a property.
- **But the owner's point is the real one:** being a *conceptual* subclass means it is **not a first-class entity** — so it cannot be reasoned about like an Object, and its **content** (raw text) is being stored **in the graph**, which is exactly what D30 says should not happen.

**⇒ The change: `:Document` folds into `:Source`**, its content leaves the graph — to **raw text in Postgres** (true binaries to the object bucket; §3) — referenced per D30, and the entities extracted from it carry the epistemic weight. **⚠️ Production has ZERO `:Document` nodes today (the commit lane has never run) — which makes the DATA migration trivial, but is NOT grounds for calling the change free.** The label is **written** (`_upsert_document`, `projection/entities.py:1527` → `MERGE (d:Document {id:$id})`), **read** (`ingest.py:188/266/307/412`; `memory_orchestrator.py` `docIndex`) and **quota-metered** (`quota.py:524`, `#1726`), and `ONTOLOGY.md` §4.4 still declares it. **The code migration, the ontology change and the meter line are the real work** — the ontology change is filed as **`#5013`**.

**✅ Cross-check against the evidence, and it agrees.** The field's nearest comparable (GAAMA, 2026) deliberately keeps a **raw episode layer verbatim** and a **separate distilled node layer** — and Microsoft's consolidation work reports **97.2% retention precision at 58% store reduction**. **Both are the same two-layer instinct: keep the raw as raw, keep the distilled as distilled, and never let one pretend to be the other.**

### 9.4 ⭐ The Source: **link first**, summary vector second — and identity is never a vector

**Owner, 2026-09-24:** *"do we vectorise the source or link to it?"* — **both, they do different jobs, and the order between them is the decision.**

**The pattern is established and it has a name: parent-document retrieval / small-to-big.** The field converges on: **embed the small units; keep the parent by ID with no vector; fetch it by walking up.** *(Databricks: "search children, return parents".)* **A parent is stored by ID *without* embedding** — because the reference is what the reader needs, not a second copy of the thing.

**① The LINK is mandatory and load-bearing — it is not the optional half.**
> **At answer time, send the source's VERBATIM text, not the extracted claim.** *(Measured: verbatim source beats LLM-extracted artefacts by **15.9 / 22.0 pts** — extractor §2.4; `#3011` would settle it on our stack.)*

**So the link is not a citation nicety — it is the mechanism that gets the evidence to the model.** Every committed `Point`/`Object` must walk up to its `Source`. **A claim with no reachable source cannot be served as evidence.**

**② A summary vector is a different capability — and it buys exactly one query class.**

| query | mechanism |
|---|---|
| *"which unit mentions the discount?"* | the **claim** vectors ✅ already have them |
| *"which of my SOURCES is about pricing?"* | ⛔ **nothing can answer this today** — needs a source-level vector |
| *"show me the verbatim text behind this claim"* | **the link** — fetch from Supabase Postgres (§9.1) |
| *"are these two sources the same document?"* | ⛔ **maximise nothing — URL + content hash** |

**This is LlamaIndex's Document Summary Index** — embed a **summary** per source, retrieve documents by summary similarity — and it changes the query shape: it returns *all nodes for a selected document* rather than matching nodes. **A different operation, not a better one.**
**⭐ We already write the summary — it is the S1 narrative (D1).** So this is not a new layer: **it is one vector attached to the `:Source` node, whose embedded text is the narrative already built.** The narrative stays in Supabase Postgres (§9.1); only the vector is new. **Cost: 2,193 × 2.05 KB ≈ 4.4 MB — and zero FalkorDB RAM if §12.1c moves the index out.**
⚠️ **The identifier-only gate applies to sources too** (§12.2): a bare `session:<id>`, or a PR with no body, earns no vector.

**③ ⛔ NEVER use a vector to decide whether two sources are the same.** Embedding similarity detects **the same TOPIC, not the same document** — it will merge two unrelated sources about pricing. **Identity is a canonicalised URL plus a content hash** (`#3998`'s absent-raw state is a third value on that record, not a fourth kind of source).

**⚠️ The honest gap, so this is not over-claimed.** **No ablation isolates a separate parent vector** — the evidence is framework practice plus measured *hierarchical* gains, and all of it is document RAG, **not agent-memory graphs.** **① is measured. ② is well-supported practice, not a measurement.** That is why the order above is the answer: **if we could only have one, it is the link.**

### 9.5 ⭐ The five consequences of "a document is a source" — ruled (owner, 2026-09-24)

**D10 is one sentence. Landing it touches five things**, and each was run through the `AGENTS.md` decision protocol (**research first, contradiction test before anything else**). The outcome is instructive: **three of the five turned out to be *preservation*, not change.** The ruling is applied in `ONTOLOGY.md` **v3.15** (PR **#5022**) — this section is the *reasoning*; the ontology is the *contract*.

#### Q1 — the `aboutDocument` link: **KEEP IT; move only its label.** ⛔ *This is the one hard block.*

Two link types look like near-duplicates:

- `aboutDocument` — *"this event is about document X"*
- `aboutSource` — *"this point is about source Y"*

**Merging them is the obvious tidy-up, and it is refused.** The two are **not** interchangeable in the rebuild machinery:

| | in the snapshot-derivable set? | moved by the live `supersede_point` transfer? | what `rebuild_all` pass-2b does |
|---|---|---|---|
| `aboutDocument` | ✅ **yes** (`DERIVABLE_STRUCTURAL_RELS`, `#2489`) | ✅ **yes** (`SUPERSEDE_STRUCTURAL_RELS`) | **re-creates it at the OLD point** from its immutable snapshot |
| `aboutSource` | ❌ **deliberately excluded** | ❌ **also excluded** | **never resurrects at old** — so it gets no replay descriptor at all |

**⚠️ The two omissions are independent, and `aboutSource` is the only `about*` rel absent from BOTH.** `aboutAction` and `wasDerivedFrom` are excluded from the snapshot-derivable set too, but `supersede_point` **does** transfer them live; `aboutSource` is moved by neither leg — a supersede leaves it on the old point, and pass-2b never re-creates it. So the exclusion is stronger than the transfer set's other members: a reader comparing the two sets must not read `aboutSource` as "excluded from replay, transferred live" like the other two.

**⇒ Collapsing `aboutDocument` into `aboutSource` moves the edge class OUT of the replayable set. That is a durability regression**, and it would be invisible until a rebuild was actually needed. **The contradiction test caught this and disqualified the recommendation that proposed it** — which is the rule working, not the analysis failing.

**What *does* change:** its **target label** (`:Document` → `:Source`), and therefore its **replay key** — `coalesce(title, name)` → **`url`**, because a `:Source` resolves by `url`. **⚠️ These two must change in the same step**: a label retarget with an unchanged key resolves to nothing and **silently mis-points the rebuilt edge**. Equal in spirit: `aboutSource` **cannot** be made derivable by this change, and doing so is real design work — filed separately, not folded in.

#### Q2 — the classification axes: **KEEP BOTH.** They answer different questions

| axis | question it answers | values | where declared |
|---|---|---|---|
| `sourceKind` | *what kind of SOURCE is it?* | `document`, `conversation`, `github_issue`, `agentSession` | **the packs** (`sourceTypes`) |
| `documentKind` | *what GENRE of document is it?* | research, planDoc, apiSpec, transcript… | the ontology (§4.4 vocabulary) |

**These are not the same question, and collapsing them is a category error** — the library world has kept *document type* and *genre* in **separate MARC fields for decades** (genre/form vs content type vs media type vs carrier type). **⚠️ A correction to my own earlier report:** an intermediate reading concluded D10 ruled `documentKind` out. **It does not.** `documentKind` survives — it just stops being an Object-subclass vocabulary and becomes a **genre axis over `sourceKind: document`**.

**⭐ And the finding that reframes the whole decision:** `document` was **already** a declared pack `sourceTypes` value (`packs/dev/manifest.yaml`) **while** `ONTOLOGY.md` §4.4 called it an Object subclass. **The repo has been asserting both models at once.** D10 is therefore **less an overturn than a choice between two models the old decisions contradict each other about** — and it makes the ontology agree with the packs.

#### Q3 — the document fields: **`doc_status` goes; `format` moves; the rest stay.**

| field | ruling | why |
|---|---|---|
| `doc_status` | **⛔ DROPPED** | §9.3's whole point: liveness is a **READ**, not a stored status. **It is also one of only two unjournalled raw-`SET` writes in the system** (`ingest.py:262-266`) — dropping it removes a write that breaks the rebuild invariant |
| `format` | **MOVES to `:Source`** | it is genuinely a source property; `_SOURCE_HANDLED` does not carry it yet |
| `documentKind` | **KEPT** | the genre axis — see Q2 |
| `title`, `topics`, `summary` | **KEPT** | already `:Source` properties (measured) |
| `content` | **⛔ LEAVES THE GRAPH** | raw text is the raw-text store's job — **Postgres** under §3/§9.1/D30; the object bucket holds only true binaries. This was the 29th site and it had gone unnoticed |
| `objectKind` | **⛔ RETIRED** | a document is not an Object |

#### Q4 — the `documents` cap: **KEEP IT, RE-POINT IT AT `:Source`.**

**Why the cap exists at all:** the main node cap counts `(:Point …) OR (:Object) OR (:Subject)`. **A `:Document` is none of those, so documents were invisible to it** — `#1726`'s own recorded rationale is *"the points gate is vacuous for Documents."* The separate `documents` resource closed that hole and gates `/v1/index/docs`.

**⚠️ The reason does not expire when a document becomes a source — a `:Source` is equally invisible to the main cap.** So:

| option | verdict |
|---|---|
| retire the cap | ⛔ **ungates `/v1/index/docs`** — the exact hole it was built to close |
| fold it into the node cap | ⛔ **silently starts metering ~2,193 sources** that were never metered — a price change disguised as a cleanup |
| **keep it, re-point at `:Source`** | ✅ **keeps both the protection and the reason** |

#### Q5 — the replay key: **fold, change the key, migrate — and it is free today.**

A `:Document` resolved by `coalesce(title, name)`; a `:Source` resolves by `url`. **A source keyed the old way would resolve to nothing and mis-point the rebuilt edge.**

**⭐ The whole risk is currently worth $0, and this is the single most time-sensitive item in D10:**

- **Production holds zero `:Document` nodes**, and
- **the commit lane creator has never run** (`commit_count = 0`).

**⇒ The migration is free NOW and stops being free the moment that lane first runs.** `#2489`'s own boundary is that **rebuild does NOT repair pre-existing graphs** (a `#2500`-style backfill is explicitly out of scope), so there is no later recovery path. **This is why Q5 cannot be deferred behind the commit lane.**

#### ⚠️ And the finding that is NOT about D10 at all — **T6**

While checking D10, a separate defect surfaced and it may be the more consequential one: **a `:Source` mutates in place.** `_upsert_source` bumps `updatedAt` / `version` / `contentHash` on an `ON MATCH` when the hash differs, and the hosted commit path flips a status — **neither is journalled.**

**That breaks §3's central invariant** (`derived = replay(journal)`) **and it contradicts the field's own rule for evidence** — the append-only/write-once convergence is explicit that *"changes are handled by new correction events, not in-place edits"*, which is also what **our own D7** says. **A source that is re-fetched and found changed is a NEW VERSION, not an edit.**

**Two options, and they are not mutually exclusive:**

- **journal the re-materialisation** — makes §3's invariant true, at the cost of log growth unless re-checks are made repeat-safe; or
- **declare the re-materialised fields *recomputable*** — which fits the field's model and must be applied **field by field, not by class**, or it becomes the same category error as Q2.

**This belongs to §3, not to D10**, and it is tracked separately.

---

### 9.6 ⭐ A source is VERSIONED — and the version lives on the extraction link as `sourceVersion` (owner ruling, 2026-09-24)

The owner raised the gap the D10 pass left open: *"shouldn't we have some form of version tracking for sources? at least to know if they changed and we might need to re-infer the Entities and check they're not superseded in a new version of a doc."* The answer is **yes**, and the model is now stated in `ONTOLOGY.md` §4.6 (`v3.15`). This section records **why it costs what it costs** — a storage question, and therefore this section's.

**The requirement was already half-stated.** The ontology's `§4.6` already defined `contentHash` as *"idempotency anchor — skip re-extraction if unchanged"* and already listed `document` under `sourceKind`. The ontology stated the requirement and **the model never completed it** — `ONTOLOGY.md` `v3.15` now states it as a **version anchor**. This change **populates declared slots** (`validFrom`/`validTo`/`expiredAt` were declared and never written) rather than adding new ones.

#### The model, in five rules

| Rule | Statement |
|---|---|
| Identity | `url` — stable across versions |
| Version | `contentHash` — identifies a **version** of that identity |
| Raw content | **append-only** — a differing hash on re-fetch is a **new version, never an edit** |
| Extraction | **version-scoped** — *"are these entities current?"* compares the **recorded** version against the **current** version |
| Version change | appends a **journal record** that **closes** the current version's window and opens the new one — never an overwrite |

**⭐ One node per `url`, and the version history lives in the journal.** `:Source` MERGEs on `url`, so exactly one node exists per source and it carries the **current** version. A version change does not create a second node and does not rewrite an older one — there is no older node to rewrite. The prior version's window is a **journal fact**, recoverable by replay (`derived = replay(journal)`, §3), the same place every other append-only record lives.

**⭐ What is superseded is the FACTS, not the source.** No Source→Source supersession edge exists or is needed. Successor facts attach to the standing `:Source` (`extractedFrom` is keyed by `url`, which a version change does not move), and the earlier facts are replaced through the ordinary `CORRECTS` mechanism. **The source is the identity; the entities are the belief.**

**And one rule that is a write-time obligation, not a later repair: closing the interval is part of the write.** An unclosed `validTo` reads as *"still true"* indefinitely — the field names this as **the #1 production bug** in temporal knowledge graphs, and it is exactly what T6 above produces today.

#### ⭐ The missing piece, in one line

**`extractedFrom` records the SOURCE, not the source VERSION.** Identity is not version. **The version rides on the link itself, as `sourceVersion`** — the `contentHash` of the version that was read (per-link, since `extractedFrom` is many→many; `ONTOLOGY.md` §4.6). Without the version on the link:

- *"are these entities **trustworthy**?"* → answerable (confidence, no NAND, not superseded — already a read);
- *"are these entities **about the content we currently hold**?"* → needs the version, and since there is **one node per `url`** the anchor must sit on the **LINK** — as **`sourceVersion`** — not on a version node.

**Both are reads; only one has an anchor.** That is the whole gap — and it is why the fix is a field on an edge, not a new subsystem.

#### ⭐ Scope of the anchor — a **Point-level** guarantee, extended to derivation links (decided 2026-09-25)

`sourceVersion` rides on `extractedFrom`, which is declared **`Point → Source`** (`ONTOLOGY.md` §3.3) — so **a class whose provenance does not pass through an `extractedFrom` link carries no recorded version read at all.** `:Object` and `:Event` are reached from a source through the `references` edge (`ONTOLOGY.md` §3.4), not through `extractedFrom`, so **neither was version-scoped before the decision below** (§4.6) — the derivation half is addressed there; the identity half deliberately is not.

**This is not a storage omission, and for the derivation half it is now decided** (below). §12.1's cost question is *where* bytes live; this is *whether the anchor exists*. Two facts follow for sizing, recorded here so they are not discovered later:

- **`references` carries more than one meaning, so the gap is not uniform.** Its declared target set is `Event | Object | Source` (§3.4), and the in-repo writers do not all mean the same thing by it:
  - **identity / mention** — a connector `Object` materialized at the projection choke point (`projection/entities.py::_materialize_connector_source`; `connectors/github.py:305` mirrors it idempotently). The Source's `url` **is** the artifact. The Source still has versions, but the target is **not read from** them, so a recorded version here would be a non-answer rather than a stale mark.
  - **derivation** — an `Event` (or `Document`) built from a source's content: the connector path materializes the link at the projection choke point (`projection/entities.py::_materialize_connector_source` → `link_source_to_event`), and the meeting path writes the same edge at `mining.py:621` (the choke point's gate excludes mining events — `ONTOLOGY.md` §3.4). Here the target **is** read from the content, and there was no recorded version to compare against — **this was the live half of the gap, addressed by the decision below**. ⚠️ **Addressed is not the same as closed on this path:** no connector writes `contentHash` and the choke point creates its Source with `contentHash = ''` (the Source MERGE in `projection/entities.py::_materialize_connector_source`), so the anchor is **absent** for connector-derived Events — honest (no version is known), but *"are these entities about the content we currently hold?"* stays unanswerable there until a connector records a content hash (tracked in `#5214`). The anchor does land on the capture path (`sha256(transcript)`) and on the repair path (`e.file_hash`).
  - **referential containment** — `Source → Source` (the session→external `MERGE (a)-[:references]->(b)` in `hosted_api.py::_execute_commit_writes`). A provenance chain, not a derivation of either target's content.

  ⇒ **The version question is a property of what the link means, not of the target's label.** That is why a blanket `sourceVersion` on every `references` edge is the wrong shape: it would stamp a non-answer on the identity and containment forms.

- **So the cost of closing it falls on the derivation links only** — one hash each, since a version is three timestamps + a hash and never a content copy (D30). ⚠️ **No total is derivable without a census**: the derivation-link count is its own quantity (the connector paths mint per-`Event` links for events that may yield no Point), so it is **not** bounded by the `extractedFrom` count. **Not measured** — the shared instance refused reads when this was written.

**DECIDED — option A (owner-approved 2026-09-25, `#5199`).** The anchor extends to the **derivation** `references` link only — targets `Event` or `Document` (`Document` retires with D10): an *optional* `sourceVersion`, set **at link time** from the version the link's own writer observes (the Source's current `contentHash`; see the repair-path caveat below) — so the **public SDK signature does not change** — and written **`ON CREATE` only**, because a re-link must not advance the recorded version or staleness would silently read as current. **Identity/mention** (`Object`) and **referential-containment** (`Source → Source`) links stay **property-free**, and a source with no content (`contentHash = ''`) anchors nothing. Currency stays a **read** (`r.sourceVersion` vs `s.contentHash`), never a stored status.

⚠️ **The mechanism discriminates on the target's LABEL, which is a proxy for "derived"** — the only signal available without changing the SDK signature. It is applied by every **provenance** writer of a derivation edge: `link_source_to_entity` (the `id`-keyed paths), `link_source_to_event` (the `eventId`-keyed connector choke point and capture path) and `link_source_to_legacy_event` (the crash-repair backfill).

⚠️ **Post-D10 the proxy is carried by the RETIRED label's alias, so it lives at the CALL SITE.** The `:Document` node label is gone (a document is a `:Source`), so the three document-derivation writers — `_upsert_document`, the session→document link in `_execute_commit_writes`, and the doc classifier of the ingest path — pass the retained deprecated alias `"Document"` as the *relation's* spelling, and `link_source_to_entity` reads it for this decision **before** remapping the identity onto `:Source`. The two facts are independent: the stored edge is `(Source)-[:references]->(Source)` either way, so switching a call site to `"Source"` keeps the edge, creates no conflict marker, and silently drops the anchor — which is exactly what the D10 merge did. These call sites are pinned by `tests/test_source_version_references_5199.py` (`test_document_derivation_through_the_production_path_anchors`, `test_index_path_document_link_records_the_version_read`, and the structural `test_document_call_sites_express_derivation_not_containment`); a definitional `grep` for the alias among them is the cheap check before any refactor of these writers.

⚠️ **This is a pipeline guarantee, NOT a graph invariant** — do not read it as "every `references` edge is anchored". Two writers bypass it by design and neither is auto-anchored: the generic escape hatch `create_edge` (public SDK, exposed as the MCP tool `tortoise_create_edge`) takes any allowlisted predicate including `references`, and `graph-scripts/backfill_references.py` is a standalone ops script. A caller minting a derivation edge through either leaves the anchor **absent** unless it supplies the property itself. Tracked in `#5213`.

⚠️ **On the repair path the anchor comes from the TARGET, not the Source.** `backfill_sources` sets the Source to the file's **current** hash while the legacy Event keeps the `file_hash` it was captured with (W2, "file edited since capture") — so anchoring `s.contentHash` there would report a **stale Event as current**, the precise failure this anchor exists to expose. `link_source_to_legacy_event` therefore anchors `e.file_hash` (equal to `s.contentHash` whenever the file has not changed), and anchors nothing when the Event records no hash.

⚠️ **KNOWN LIMITATION — the anchor does not advance when the TARGET is rebuilt in place; the read is then STALE for a current entity.** `ON CREATE` is a property of the *edge* MERGE, so a derivation target rebuilt from a **newer** version through the **same node id** keeps its **original** `sourceVersion`. One real path: `index_directory` → `_index_directory_locked` → `_index_process_unit` (`sdk.py`), where `_index_source_merge` bumps the Source's `contentHash`, `_session_event_write` rewrites the existing Event **in place** under the same `eventId` (`event_id = f"session_{session_id}"` — content-independent), and then `link_source_to_entity` MERGEs an **already existing** edge, so `ON CREATE` does not fire. (`ingest_corpus` is **not** such a path — it rewrites Events but never writes a Source, so it never mints this edge.) The currency read then reports STALE although the target is current: the **converse** of the false-current guarded above, and the **conservative** direction the model prefers (`stale != wrong`, §4.6 — the entity is `stale`-flagged, never hidden or deleted). It is deliberate that this is not "fixed" by removing `ON CREATE`: that would re-open the false-current on a **source-only** re-poll (a connector re-poll that bumps the Source with no target rebuild), and the ON CREATE/ON MATCH pair cannot distinguish the two cases on its own. Recorded for the owner on `#5199`.

⚠️ **No backfill, deliberately.** An edge written before this change carries no recorded version and is **not** retro-stamped: the version it was read from is **unknown**, and writing today's hash would fabricate a `current` read. Its honest state is **absent** — which is precisely why the anchor is a derived comparison and never a stored `status`.

⚠️ **The model statement lives in `ONTOLOGY.md` §3.4 / §4.6** — this section records only what the anchor costs. The ontology wording is in owner review (`#5199`); until it lands, the code and this section are the operative record.

#### ⭐ The policy — **B: mark stale now, supersede on re-inference** (owner)

When a re-fetched source's content differs, the old version's entities are **marked stale immediately** and **superseded when re-inference produces their successors**.

**Not A (immediate supersession).** A withdraws the belief *before* producing its successor — between the source changing and re-inference running, the graph asserts **nothing** about a subject it previously had a position on. A stale-but-present belief is strictly better than no belief.

**And `stale ≠ wrong`.** Supersession is **additive**: the old facts are marked, never deleted. The same rule as D7, and the same rule the journal follows.

#### ⭐ Why it is affordably cheap HERE and expensive elsewhere

The field's own warning is that **"versioned KGs are resource-intensive"** — true of systems that copy the artifact per version. **D30 already moved content out of the graph**, so for us a version costs:

> **three timestamps and a hash — not a copy of the artifact.**

**Bound it there: windows and hashes only, never content copies.** If a version ever starts carrying bytes, this stops being cheap and §13's cost argument changes. The content lives in **Postgres** (§9.1) once, addressed by `url` + `contentHash`.

#### ⭐ Why document-level versioning ALONE would not have answered the owner's question

The convergence is on **two layers with different jobs**: the **source** carries the version and its window; the **facts/edges** carry `valid_at`/`invalid_at`/`invalidated_by`, **invalidated rather than deleted**. One practitioner account **started at document-level supersession and revised to move time onto the relationship edges** — because if you supersede only the document, **the facts are untouched**, and *"what did we believe before?"* is a question **about facts**. Document-level is **necessary but not sufficient**.

**⇒ This is why the version goes on the extraction link and not only on the source** — and it is why the storage cost lands on the edge, not on a document store.

**Research:** `docs/research/2026-09-24-source-versioning/research-brief.md`. **Adoption gate: ADOPT** — no recorded decision contradicted; D7 governs it; the ontology's `§4.7` already declares the Source window; D30 bounds the cost. **Tracked:** `#5038` (the model) · `#5024` (the in-place mutation that blocks it) · `#5025` · `#5026`.

#### ⭐ Where this stands (2026-09-25) — a status pointer, not a new rule

- **The anchor shipped.** The read version rides on the `extractedFrom` link as `sourceVersion` (`#5256`, PR `#5288`), with the honest-absent rule (**`''`/blank/non-string hash ⇒ no property at all**), live == replay, and **no SDK/MCP surface change**.
- **The currency CHECK is a read** (this section's model): it compares the recorded `sourceVersion` against the source's current `contentHash`, with **no stored `status`** field — so it belongs on the **existing** read surfaces and needs **no new tool and no new SDK method**.
- **What a stale fact does — Policy B, REFINED by the owner on 2026-09-26** (`#5038` comment `5845802608`): *"We should not return an out-of-date fact when we have a newer one"* — so when a newer fact exists, the out-of-date fact is **withheld as an answer** and **disclosed as an FYI carrying its source** (*"let the user know that (newer fact but no source, and older fact from source X) … so I can disambiguate"*). This **refines rather than reverses** Policy B: Policy B's prohibition was on the fact being *"silently"* withdrawn, and the required disclosure is exactly what keeps this non-silent. **OVERRIDES:** the field's practice of returning a stale fact **alongside** its replacement with a flag (Zep/Graphiti's temporal fields; the RAG `is_latest` norm) — the withheld-as-answer + disclosed-as-FYI form is deliberate, because a visible flag is measurably not acted on (`arXiv 2609.08258`; `2605.06527`: 77.5% visible vs 3.3% adjudicated). The **write-time** notice the owner also asked for (*"ideally at write time it would have told me"*) is **`lane:c1-capture`'s** surface, not this one. **⚠️ Open alongside it:** how mechanical validity reconciles with **EP confidence** (owner, same comment — *"not sure how we reconcile the two here"*), tracked as **O9** in `docs/plans/2026-09-25-5038-source-version-anchor.md`; research first, and no second truth signal is added beside EP in the meantime.
- **A non-re-read successor records no version** (owner, O1) and reads `unknown` until it is itself read from a source; `ONTOLOGY.md` §4.6 is unmodified and the §4.6 reopen was not taken.
- **The re-inference step has no owner** — filed as **#5422**, because acceptance A2 of `#5038` cannot complete without it. **`#5024` is its precondition** (a re-fetched `:Source` currently mutates in place, unjournalled: if the old version is overwritten there is nothing to mark stale).

*This pointer records status only. `ONTOLOGY.md` §4.6 is owner-gated and is NOT edited by this work.*

---

## 10. Two storage patterns worth taking from Hindsight (2026-09-23)

The extractor doc §§11–13 carry the full verification. Two findings are **storage** decisions:

### 10.1 Invalidate by RELOCATION, not by a flag

Hindsight's `{"state":"invalidated"}` **"does not set a flag to be filtered later. It moves the row out of the active table into a separate archive … So recall needs no state predicate… no query pays for your cleanup."** Causal edges are **snapshotted onto the archived row** (so the archived fact still explains itself), and the move is **reversible**.

**Why it matters here:** it means the **hot table stays the working set** — which is exactly the invariant this whole document rests on (*cost scales with the working set, not with total stored data*). A status column that every query must filter would put the entire history back in the hot path. **Proposed, composes with D7's appended-`Event` lifecycle: the append is the record; relocation is what keeps the read path small.**

### 10.2 Provenance ids are an ALLOWLIST, never an exclusion list

Because `based_on` carries ids that address **different tables**, a "which of these went missing?" check written as an **exclusion** list **reports every one of them as missing.** Transferable bug — write the check as an allowlist.

---

## 11. Keeping the connection layer cheap — owner ruling (2026-09-23)

**Owner:** *"having those epistemic and so connections is our product. that's what we pitch. So we need to keep them. Question is if there's a cheap way to keep them so they're not using RAM always."*

**Ruling: the connection layer IS the product and it is KEPT.** The cost problem is solved by **where it is stored**, never by making it smaller or deriving it away.

### 11.1 Why the connections are not the cost problem — measured

Our `aboutObject` links are **property-free**: a full-repo search found **zero** `aboutObject` edges carrying any property (`rg -n 'aboutObject \{'` → **0 matches / 1,842 files**). **An edge that is two ids and nothing else is the cheapest row a store can hold.**

Estimated footprint in Postgres (two 16-byte ids, one B-tree index, standard tuple + index overhead):

| scale | edges | on disk | at Supabase **$0.125/GB/mo** |
|---|---|---|---|
| today | 27,310 | **~2.5–3 MB** | **~$0.0004/month** |
| 10× | ~273,000 | **~30 MB** | **~$0.004/month** |

`[ESTIMATED — from the measured property-free finding plus standard Postgres row/index overhead. Not measured on our schema.]`

**⇒ The connection layer is ~2% of the 140 MB graph. It is not what costs money.**

### 11.2 What actually costs money, restated

FalkorDB requires **the entire graph in RAM** — 140 MB × $73/GB/month ≈ **$10/month, whether or not any of it is read.** In Postgres the same bytes sit **on disk at $0.125/GB/month**, and RAM is only a cache. **The connections are cheap; RAM *residency* is expensive.** This is §2's mechanism applied to the exact thing the product is built on.

### 11.3 Two different things, both called "connections" — they must not be confused

| | what it is | storage |
|---|---|---|
| **Entity links** (`aboutObject`, `aboutSubject`) | a bare *"this claim mentions this thing"* | **property-free rows** — the cheapest possible form |
| **Epistemic operators** (`IMPL`, `NAND`) | **not edges — they are `Point`s** (2,233 of them, **8.9% of quota**), plus 4,748 joining edges | a **node** carrying EP confidence, so it *must* hold data |

**The epistemic layer is already half-node, half-edge** — which is why it can carry a confidence and an argument while the entity links stay bare. **That split is what makes the whole layer affordable: the expensive, data-bearing part is small and deliberate; the numerous part is a bare id pair.**

### 11.4 The four rules that keep it cheap

1. **Keep entity links property-free.** Attaching a weight or a date to every link turns 27,310 bare rows into 27,310 rows you must maintain *and index*. Weight and time belong on the **claim**, or on the **operator node** — not on the link.
2. **Cap the fan-out** *(adopted 2026-09-23, §11.5)* — bounds how many edge pages one hub query pulls, which is the working-set bound this whole document rests on.
3. **Index the direction you query**, not both by reflex — each direction is a second B-tree over every row.
4. **Let cold edges stay cold.** An old session's links are never queried, so in Postgres they are never paged in — they cost disk and nothing else. ⚠️ **This is the property FalkorDB cannot give**, and it is the entire reason the migration matters for this layer.

### 11.5 Fan-out cap — ADOPTED (owner, 2026-09-23; decision protocol)

**Nothing in our write path bounds how many links one entity accumulates.** Measured hubs: `config/ci-surfaces.yml` **123** · `durations map` **111** · `the admin-merge rail` **70** · `cal-trigger.py` **64** · `the plan doc` **63**. Hindsight caps at **200** and *also* carries a timeout that drops the whole expansion arm.

**Adopted in principle; initial value 200** — the comparable's proven value, and **above our current worst hub (123), so it binds nothing today and cannot lose data now.** It is a **guard rail against the runaway case, not an optimisation**; refine with real usage (owner, 2026-09-23: *"we haven't launched yet … we optimise with users"*).

⚠️ **Set the value against the right quantity.** The cap is a **working-set bound** — it limits how many link pages one query pulls into memory at once. It is **not** a quality judgement about which links matter. **Do not lower it to "reduce nodes"**: §11.1 shows the links are ~3 MB, so a lower cap buys no meaningful storage and risks losing a real connection, which is the product.

⚠️ **Note the twist:** deriving `aboutObject` is what *creates* the fan-out problem — a join on a 1,200-claim hub yields ~1,200 intermediate rows **per anchor**, which is exactly why Hindsight needed a cap *and* a signal-dropping timeout. **So the cap is both the guard rail for keeping the edge and the precondition for ever deriving it.**

### 11.6 What would make this layer expensive (so it is not discovered later)

- **Unbounded fan-out** — a hub query pulling tens of thousands of edges into memory at once.
- **Properties on the link** — see rule 1.
- **A query shape that scans instead of seeks** — any lookup not on an indexed id.
- **Putting the links in a store that requires RAM residency.**

### 11.7 The exception, unchanged

**Entity links are not vectors.** Keep them separate and the RAM question never touches the connection layer.

⚠️ **The claim that used to sit here — *"Vectors are the one layer that does not follow the disk rule (§2) — `pgvector`'s HNSW index is RAM-resident"* — was STALE and is removed** (corrected 2026-09-24). It described a **Postgres** target (§12.1c is the open vector-home question), and it rested on the RAM-residency premise that **§2.1 and §12.1 correct**: `pgvector`'s HNSW index is an **ordinary page-cached index that need not fit in memory** (`docs/research/2026-09-23-vector-index-ram-model/`). **It is also not a description of FalkorDB**, where the engine is in-memory throughout (§2.1) and no layer "follows a disk rule" at all. **The conclusion of this section is unchanged either way: the connection layer is not a vector, so this question never reaches it.**

---

## 12. Hot / warm / cold — what actually has to be in RAM

**Owner, 2026-09-23:** *"how do we decide what to load into the graph to optimise RAM usage (and keep our budget from exploding)? I imagine most queries don't need the epistemic layer, but then some need TLDR epistemic/events related to an object/subject, and some rare queries require the full (or at least fuller) data around an entity so deeper epistemic, more events too."*

**⚠️ The research reframes the question, and the reframe is the most important result in this document.**

### 12.1 The vector index — the class that dominates our bill

**⚠️ THIS SECTION WAS REWRITTEN TWICE. Read the version below, not either earlier one.**

- **Draft 1** claimed the index *"is RAM-mandatory and does not follow the disk rule"* and inferred an unprovisionable **7 TB** of RAM.
- **Draft 2** corrected that to *"disk-backed and page-cached"* — **correct for pgvector, but written for the then-deferred Postgres target.**
- ✅ **Draft 3 (this one) is about the engine we actually run.** ⚠️ **And the 2026-10-08 ruling (§2.0) makes that Postgres target the RECORD** — so Draft 2's mechanism is now live for the record store, while this section continues to describe the cache.

**On FalkorDB the index is in RAM, and there is no exception.** FalkorDB has no spill mode, no pageable index, and no eviction (parent §2.1) — so **the index is a permanent fixed cost of the account, active or idle.**

**And it is the biggest single class we pay for** — ⚠️ **and the exact attribution is now MEASURED, replacing an earlier estimate.** `GRAPH.MEMORY USAGE` on the live graph (2026-09-24, `SAMPLES 100`):

| | MB | of the graph |
|---|---|---|
| **`total_graph_sz_mb`** | **141** | 100% |
| `indices_sz_mb` (**all** indexes) | **45** | 32% |
| — `Point` node attributes | **50** | 35% |
| — `Object` node attributes | **13** | 9% |
| — `Event` node attributes | **9** | 6% |
| — `GraphEvent` | 11 | 8% |
| — `Source` · matrices · edges · misc | ~13 | 9% |

**Stored vector properties ≈ 50 MB** (`Point` ~31 MB + `Object` ~11.8 MB + `Event` ~7.2 MB at 1.5 KB each), **plus the HNSW portion of the 45 MB index total.**
⚠️ **Correcting an earlier draft that said *"95.6 MB of 140 MB (~68%)"*.** That figure **added the whole index total** — but the index total also contains **every fulltext index we have** (`Point.content`, `Point.search_keys`, `Object.name`, `Event.subject/name`, `Subject.name`, `Document._searchText`). **The honest answer is a range, not a point: the vector class is somewhere around half the graph, and the precise split needs an index-level breakdown we do not have.** *(What is certain and measured: `Point`, `Object` and `Event` attributes are 72 MB of which the bulk is 1.5 KB embeddings; `indices_sz_mb` is 45.)*

**⇒ Therefore "what earns an embedding" is the largest lever on our bill that requires no engine change and no migration.** That is §12.2's subject. **And see §12.1d — the latency half of this debate is now ANSWERED with our own measurements, so the only open question is cost.**

#### ⭐ A vector is POINTER-SIZED — 1.5 KB, whatever the text says

**Owner, 2026-09-24:** *"I don't want the whole summary vectorised if that needs to go to FalkorDB as it would be too heavy in FalkorDB RAM. We decided to keep summaries in Supabase for that reason."*

**The worry is right in spirit, and this is the shape in which it does not bite:**

| | size | does it scale with the text? |
|---|---|---|
| the summary **text** | a few KB | ✅ yes |
| its **vector** | **1.50 KB** stored | ⛔ **no — fixed width** |

**A 3 KB summary → a 1.50 KB vector. A 50 KB transcript → the same 1.50 KB vector.** A 384-dim float32 embedding is a fixed-width row: **it is a pointer, not a payload.** So *"vectorise the summary"* does **not** move the summary into the graph — **D1 is untouched in substance (the text is still not a graph node), and the text lives in Supabase Postgres (§9.1, destination corrected 2026-10-09), so vectorising it changes nothing about the RAM problem.**

**Cost of a vector for every Source we have: 2,193 × 2.05 KB ≈ 4.4 MB — about 3% of the 140 MB.** *(2.05 KB is the **resident** figure: 1.50 KB of payload plus its share of HNSW index overhead. The 1.50 KB above is the **stored** row.)*

**⇒ But the instinct points at the real heavyweight, and it is not the summaries — it is the vectors we ALREADY store.** See §12.1b, and the open decision at §12.1c.

#### What is NOT a problem: latency (measured 2026-09-24 — an earlier draft got this badly wrong)

**An earlier draft claimed a cold index produced *"p90 96ms vs 24ms"* and that this was *"20–50× over every published latency budget, so it cannot be user-facing."* BOTH HALVES WERE WRONG.**

**Wrong half 1 — the number was not ours.** 96ms/24ms came from **OpenSearch's designed disk mode** — a different engine. And the **"10,500ms cold"** figure came from a **billion-vector AWS deployment**. Our dataset is 140 MB. **Neither describes us.**

**Wrong half 2 — the budget is not a requirement.** The *"50–500ms"* figures are **vendor self-report**:

| claimed | actual origin |
|---|---|
| *"sub-200ms"* | **Zep's own marketing** — its own 155ms p95, **no methodology published** |
| *"50ms"* | an **asserted table** in a vendor blog, no benchmark |
| *"Nielsen 0.1s/1s/10s"* | **human-facing UI** thresholds — applying them to an internal pipeline step is **not established** |

⭐ **And the same competitor post the budget came from says the opposite of what was quoted:** *"there is no universal 100ms or 200ms requirement… Set that target for the interaction you are building. A live voice turn, a support chat and a background research task have different constraints."*

**The only published measurement (Mem0's paper): search p50 148ms / p95 200ms, against a whole turn of p50 708ms / p95 1.44s — retrieval is ~20–25% of what the user waits for, not a wall.** And **no study exists** linking 50ms vs 300ms retrieval to agent outcomes.

**Our realistic equivalent, index resident:** ~**tens of thousands of vectors → single-digit ms**; at month-6 size (~300–500k vectors) **under 50ms**.

**⇒ CONCLUSION: latency is not a constraint at our size and is not a reason to change anything.** Recorded because the earlier draft would have justified an architecture on it. *(Retrieval genuinely sits before the model starts, so it is not free — it is simply small.)*

✅ **AND THE GAP IS NOW CLOSED — M1 was taken 2026-09-24 (§12.1d): our own retrieval layer is 1.5–5 ms.** Every number above has been superseded by ours; **latency is a non-issue and the only open question about the vector index is cost.**

---

### 12.1a Why "just partition per tenant" is NOT one mechanism — and the two halves have OPPOSITE answers

**⚠️ CORRECTED 2026-10-08 by this lane's own first-party measurement. An earlier version of this section withdrew per-tenant partitioning wholesale, on a study whose mechanism does not reach declarative partitioning. It conflated TWO different mechanisms, and they behave oppositely.**

**The two mechanisms, and they are not interchangeable:**

- **(A) a PARTIAL INDEX per tenant on ONE relation** — every tenant's HNSW index lives on the *same* table.
- **(B) DECLARATIVE PARTITIONING (`PARTITION BY LIST (tenant_id)`)** — each tenant's index lives on its **own** relation.

**§12.1a's cited mechanism — *"Postgres takes a shared lock on **every index of a table** during planning"* — can only reach (A).** It is a count of indexes **per relation**, so it cannot reach (B), where each partition is its own relation. ⚠️ **Treat it as a hypothesis about PLANNING COST, not as an observed ceiling: this measurement saw no lock exhaustion in any branch, at any tenant count up to 2,100.** What (A) demonstrably pays is a per-relation planner cost that grows with the number of indexes on that relation — enough to reject (A) on measurement, without asserting a wall. The earlier draft's conclusion (*"one shared index with a tenant filter"*) followed from (A) and does not apply to (B).

**FIRST-PARTY MEASUREMENT (Postgres 17.11 + pgvector 0.8.7, 384-dim, one kNN query per tenant, `EXPLAIN ANALYZE` planning time):**

| tenants | **(A) partial index/tenant** | **(B) `PARTITION BY tenant`** | **(C) size-earned conditional** |
|---|---|---|---|
| 10 | 6.858 ms | 6.362 ms | — |
| 100 | 12.463 ms | **1.789 ms** | 0.607 ms |
| 500 | **83.304 ms** | 6.863 ms | 0.542 ms |
| 2,100 | 75.476 ms | 96.603 ms | **0.714 ms** |
| **indexes at 2,100** | **2,101** | 2,100 | **7** |

- **(A) degrades with tenant count** (~12× from T=10 to T=500). **(B) is flat to T=500** (6.4 → 1.8 → 6.9 ms). ⚠️ **BOTH branches break non-monotonically at T=2,100 — (A) FALLS to 75.476 ms (below its own T=500 value) and (B) JUMPS to 96.603 ms — and neither outlier is explained.** This is a *planning*-time table over thousands of indexes/partitions, so **do not quote either branch as monotone, or (B) as "flat", at T=2,100.** **`DELETE` succeeded in EVERY branch; no lock exhaustion was observed.**
- **The earlier table's column was MISLABELLED** — it was headed *"recall p50"*, but the values it carried (154 ms, 184 ms, 2.3 s, ~11 s — **the superseded study's own figures, at its own index counts**) are **milliseconds**. It is a LATENCY column. ⚠️ **Its population is NOT this table's: the superseded study counted 51 indexes at T=10, while branch (A) here carries 2,101 at T=2,100 (~11 at T=10) — so the two are not the same quantity.** What this measurement supports is that **a per-tenant partial index remains usable at the counts tested**; it is not a point-for-point correction of the old study at its own counts.

**⭐ THE DECISION — and it is neither (A) nor (B) wholesale: the SIZE-EARNED CONDITIONAL INDEX (C).** *"The winning property is BOUNDED RELATION COUNT, and the size-earned index achieves it by making the index CONDITIONAL ON VOLUME — a byte-shaped axis, not a tenant-count-shaped one."* **7 indexes at 2,100 tenants; 0.71 ms there (0.54–0.71 ms across T=100–2,100).** This is why (C) wins: it is the only branch whose cost axis matches the thing that actually grows.

**⚠️ And the correction to the *reasoning* matters as much as the numbers: the withdrawal took a PER-RELATION PLANNING COST and applied it to a mechanism it cannot reach.** ⚠️ **Read it as a cost, not a ceiling — no lock exhaustion was observed in any branch, at any count tested.** *"A cost analysis would never have found it"* was true of (A) — and it was equally true of the conclusion drawn about (B), which nobody had measured until this table.

⚠️ **`pgvectorscale`/DiskANN is NOT available on Supabase** (confirmed on the live extensions list; two open requests `#27474`, `#29095`). `pg_prewarm` **is** available.

#### How big our index is, and why "fewer vectors" is the whole game

```text
footprint_per_vector ≈ 1.1 × (4d + 8M) bytes
d = 384, M = 16 → 1,830 B ≈ 1.79 KB/vector
```

**Our measured reality: 33,580 embeddings ≈ 51.6 MB of vectors, plus a 45 MB index.** Today's graph carries **1.34 embeddings per node** (25,000 nodes, 33,580 embeddings) — so **each extra node costs roughly 1.34 vectors of RAM on top of its own**. That ratio, not the raw node count alone, is what drives the VECTOR line — node count is an INDEPENDENT lever on the INDEX line (§7).

**⚠️ The single most valuable number to stop guessing:** *(caveat kept from the earlier draft)* **`2.5M` has ONE meaning in this document: the product total.** An earlier draft used it a second time for *"one tenant"*, which read ~1,000× smaller — the silent scale flip behind the 7 TB scare. **That second usage is deleted, so any occurrence here means the product total.** If a second meaning is ever needed, give it its own symbol rather than reusing this one.

✅ **MEASURED 2026-09-24 — M1 is done. See §12.1d: 1.49 ms network floor, 2.74 ms indexed vector search, 1.55–1.75 ms traversals. Our retrieval layer is single-digit milliseconds and latency is not a constraint.**

### 12.1b ⚠️ CORRECTED ACCOUNTING — where the vectors actually sit (2026-09-24)

**An earlier draft attributed the whole vector class to one index. Reading the code changed the picture; then the live graph changed it again. One finding is a filed defect.**

**There is exactly ONE vector index in the entire graph — and it is on `Point`.**

`CALL db.indexes()` on the production graph, 2026-09-24 — **the complete inventory, not a sample:**

| label | VECTOR field(s) | FULLTEXT field(s) |
|---|---|---|
| **`Point`** | ✅ **`embedding`** | `content`, `search_keys` |
| `Object` | ⛔ **NONE** | `name` |
| `Event` | ⛔ **NONE** | `subject`, `name` |
| `Source` | ⛔ **NONE** | — |
| `Subject` | ⛔ **NONE** | `name` |
| `Document` | ⛔ **NONE** | `_searchText` |
| `Session` / `GraphEvent` | ⛔ **NONE** | — |

**⇒ 12,657 embeddings — `:Object` 7,859 and `:Event` 4,798, i.e. 38% of every vector we store — are stored and paid for with no index behind them.** ≈**19 MB, 13.5% of the graph.** `Object`'s node attributes are **13 MB, of which ~11.8 MB is embeddings — the label is mostly vectors.**

**⭐ And they are still queryable — by full scan, silently.** Measured through the app's own path (`run_vector_query(..., entity_type=…)`, 14 runs, 4 discarded as warmup):

| `entity_type` | p50 | p95 | indexed? | `leg_trace.reason` |
|---|---|---|---|---|
| `point` | **4.97 ms** | 5.64 ms | ✅ yes | **`ok`** |
| `event` | **13.40 ms** | 13.82 ms | ⛔ no | **`ok`** |
| `object` | **16.96 ms** | 19.01 ms | ⛔ no | **`ok`** |

**`:Object` costs 3.4× `:Point` for the same query, and both report `ok`** — the trace **cannot distinguish an index hit from a table scan**, so degradation is unobservable to any caller. **⇒ Filed as `#4999`** (`degraded=False, reason="ok"` on the brute-force path; the only signal is a log line).

**⚠️ The mechanism, and it is a drift, not an oversight:** `#172` (`[P1][bug] run_vector_query lacks entity_type`, closed 2026-08-06) made the **query label dynamic** — `label = entity_type.capitalize()`. **The index creation was never made dynamic**; `_ensure_indexes()` still carries the literal `'Point'`. **So the query side advertises four entity types the index side never created — and by making those paths return rows, the fix also made the gap invisible.**

**⇒ Filed as `#4997`. ⚠️ REFRAMED 2026-09-24 (owner): the *"stop storing OR index them"* framing was a FALSE CHOICE.** The third option is better than either: **keep the vectors, make them searchable, and put them where RAM is cheap — i.e. this is the SAME decision as V1.** See §12.1c. **The one defect that is NOT gated on V1 is filed separately as `#4999`:** the trace reports `ok` for a full scan, so degradation is unobservable to any caller — **the same shape as a silently-switched backend:** the system keeps working, more expensively, and nothing says so. **Fixing `#4997` would not fix it** — a future missing index, or a future linear-scan wall, would still report `ok`.

**★ Practical footnote for `:Source`:** `Source` carries **no** embedding and has **no** vector index — but it **already holds `summary`, `topics`, `url` and `contentHash`**. So §9.4's summary vector would be **indexing an existing field**, and §9.4 ③'s *"identity is URL + content hash"* is **already implemented**. *(Avg `Source.title` = 44 bytes — a Source's own text is tiny, so its 1.5 KB vector would be ~35× its title.)*

### 12.1c ⭐ OPEN DECISION — should the vector index live in FalkorDB at all?

**Raised by the owner, 2026-09-24:** *"I don't want the whole summary vectorised if that needs to go to FalkorDB as it would be too heavy in FalkorDB RAM… or is the idea to vectorise summaries but keep them in supabase (is that even a thing?)"* — **yes, it is a thing, it is the standard pattern, and the question is bigger than summaries.**

**Context.** FalkorDB charges for **provisioned RAM**, not for graph size, with **no eviction** — so every stored vector is permanent rent (§2.1). Measured (§12.1b): **≈50 MB of stored vector properties + `indices_sz_mb` 45 (of which only part is the `Point` HNSW index)**, against **141 MB total** — around half the graph. Everything else — every entity, claim, operator and edge — fits in the remainder. **A vector index needs RAM. A graph needs traversal. Those are two different requirements, and only one of them is expensive.**

**Options.**

| # | option | RAM | latency | what it costs us |
|---|---|---|---|---|
| **1** | **vectors stay in the graph** (today) | — | in-graph `queryNodes` | **~half the graph's RAM** (≈50 MB stored vectors + a 45 MB index total of which part is HNSW) |
| **2** | **vectors move to Supabase Postgres (`pgvector`); the graph keeps the node and the edges** | **−≈50 MB of vectors − the HNSW index** | **+~10–15 ms, measured-equivalent** | **a cross-store join: *"similar to X **and still live**"* becomes two hops** |
| **3** | vectors in a dedicated vector store | same as 2 | similar | **a third system to run** — and it buys nothing over option 2 at our size |

**Analysis.**

- **The latency cost is noise at our size.** pgvector resident is **8–18 ms at 1M vectors**; in-memory is **4–5 ms** — a **5–15 ms** difference against a model call measured in hundreds to thousands of ms. **The published variance between engines is smaller than the run-to-run variance of one engine** (§12.1).
- **This is the pattern the architecture already uses.** Raw files and the narrative already live outside the graph, **reached by a reference** — D30 and §9.3. **Moving vectors out is the same move applied to one more asset class.** *"Vectorise it but keep it in Supabase Postgres"* is precisely that: the vector is the reference, and the text never moves.
- ⭐ **AND FALKORDB CHARGES PER-LABEL, WHICH MAKES THIS STRONGER THAN A COST CHOICE (owner's synthesis, 2026-09-24).** HNSW indexes here are created **per `(label, property)`** — `createNodeIndex('Point', 'embedding', …)`, and `CALL db.indexes()` confirms `Point` is the only label with a vector field. **So *"index the objects"* means a SECOND HNSW index for `:Object` and a THIRD for `:Event` — three resident copies of index structures on the most expensive resource we have.** Postgres gives the same capability from **one** index over a shared table, filtered by kind (`WHERE kind = 'object'`), with partial indexes available later if a kind deserves isolation — **which is exactly the deliberate split Hindsight does per `fact_type`.** **⇒ Three resident HNSW indexes vs one filtered index.**
- ⭐ **And the two jobs in the recommended architecture have different resource needs.** "The vector picks the ENTRY node; the graph does the reasoning" (GraphRAG, Neo4j) — **ANN wants cheap bulk memory; traversal wants the graph.** **So the split store is not a compromise: it is the natural shape of the architecture the research describes.** The cost-optimal layout and the recommended architecture are the same picture.
- **What genuinely is lost:** single-query hybrid search. Anything needing *both* a similarity ranking **and** graph state (liveness, EP confidence, an operator) becomes two queries joined by id. **The join is on a top-k result set, so it is cheap — but it is a real change to the retrieval path, not a tuning flag.**
- **It does NOT change the engine.** §14.2 keeps hosted FalkorDB. This changes **what FalkorDB holds** — and it is the largest cost lever available with **no migration and no rewrite.**

**⭐ Recommendation.** **The measurement now exists (§12.1d) and it supports option 2.** Our whole vector leg is **2.74 ms indexed**; a **full linear scan of 7,859 Object vectors is 9.26 ms**; our network floor is **1.49 ms**. **So the index is worth ~6 ms at our size, and a cross-store hop to Supabase Postgres would land around ~10–15 ms — noise against a model call.** The index becomes worth having at month-6 (~300–500k vectors → a scan would be ~300–500 ms), **which is exactly the case for keeping the index but not renting FalkorDB RAM for it.**
**⚠️ NOT DECIDED — owner's call.** Recorded as an open decision (§14.3 V1) rather than adopted, because it changes what the graph holds.

**And it removes the source-vector objection entirely:** if the vectors live in Supabase Postgres, giving a Source a summary vector (§9.4) costs **zero FalkorDB RAM** — it is a row in a table we are already paying for.

### 12.1d ⭐ M1 — OUR OWN LATENCY, MEASURED (2026-09-24)

**This section existed for a day as *"we have never measured our own query latency"*. We have now. It ends the latency half of the debate.**

Against the live production graph, warm, 6–12 samples per class (`/tmp/lat.py`, executed on the app itself):

| class | p50 | p95 |
|---|---|---|
| **network floor** (`RETURN 1`) | **1.49 ms** | 1.57 ms |
| count any label | 1.44–1.49 ms | 1.53 ms |
| **vector search k=10 (HNSW, indexed)** | **2.74 ms** | 3.18 ms |
| vector search k=50 | 3.71 ms | 4.13 ms |
| fulltext | 1.70 ms | 1.80 ms |
| **1-hop traversal (claims about an Object)** | **1.55–1.63 ms** | 1.68 ms |
| **2-hop (chain → its Source)** | **1.75 ms** | 1.81 ms |
| 3-hop (Object → claim → operator → claim) | 2.35 ms | 6.01 ms |
| hub fan-out, 200 rows (the 123-claim hub) | 3.15 ms | 3.26 ms |
| **full scan of all 7,859 `:Object` vectors** | **9.26 ms** | 9.58 ms |

**⭐⭐ THE ANSWER: our entire retrieval layer runs in single-digit milliseconds — and the *whole vector leg* is ~3–5 ms against a 1.49 ms network floor.**

**Three things this settles:**

1. **Latency is not a constraint, and never was** (§12.1). It is **~1.5–5 ms** against a model call in the hundreds-to-thousands. **A cross-store hop to Supabase Postgres would land around ~10–15 ms — still noise.** The 5–15 ms the research predicted is confirmed as irrelevant at our scale — **so the ONLY open question about where the vectors live is cost** (§12.1c).
2. **The index earns very little today and more later.** An HNSW lookup is **2.74 ms**; a **full linear scan of all 7,859 Object vectors is 9.26 ms** — **so the index is worth ~6 ms at our size.** At month-6 (~300–500k vectors) a scan would be ~300–500 ms, **so it earns its keep later, not now** — and that is precisely the case for keeping the index but not renting FalkorDB RAM for it (§12.1c).
3. **⭐ Traversal is cheap enough to be the default.** *"How does this Object relate to events, subjects and points — the why, who, when"* is answered by **1.55–2.35 ms walks** — **and needs no vector at all** (§12.2c).

### 12.2 ⇒ The lever is "what gets an embedding"

**Every other layer can be small; the vector class is the one we pay RAM rent on** — **≈50 MB of stored vector properties plus a 45 MB index total (of which only part is the `Point` HNSW index), against 141 MB** (§12.1b). Every vector is a fixed cost of the account, **active or idle** (parent §2.1), whether or not it is ever matched.

**⚠️ Correction from the owner's counterexample (2026-09-23).** An earlier draft of this section proposed leaning on **class** ("don't embed Events / Objects / mechanical statements"). **The owner falsified it:** *"Events — when deterministically named? that's often but not always the case, a meeting has an authoritative title from the calendar sometimes but not always."* **A class is not a valid unit** — some Events carry meaning (`"Q3 planning sync with the infra team"`) and some are pure identifiers (`merged PR #465`). **The valid unit is the TEXT:**

> **A row earns a vector if its embedded text carries meaning that its identifiers do not.**

| embedded text | meaning beyond ids? | vector |
|---|---|---|
| `Event{merged, PR #465, 2026-09-19}` | no — fully addressed by (object, type, time) | **no** |
| `Event{meeting, "Q3 planning sync with the infra team"}` | **yes** | **yes** |
| `Point{"p99 latency is 480 ms at 3k rps"}` | **yes** | **yes** |
| `Statement{"cal-trigger.py is at line 42"}` | no — identifiers | **no** |
| `Object{"the plan doc"}` | no — a name; matched by **lookup** | **no** |

**Consequences of the text rule, and each one matters:**

- **No class is wholly excluded — including `Point`s.** A Point whose content is *"see PR #465"* is as identifier-only as an Event.
- **`Event`s and `Object`s are treated identically** — the class carries no information.
- **The gate is at the EMBEDDING step, and it is a NEW gate.** ⚠️ It is **not** `#4899`'s `DISCARD`: `DISCARD` decides whether the **row exists**; this decides whether the row **keeps a vector**. **A row can be worth KEEPING but not worth EMBEDDING** — a merge `Event` is exactly that (it is needed for the lifecycle fold and the audit, and its text is identifiers).
- ⚠️ **`classify_consolidation` (`extractor_v2.py:3021`) is NOT this gate** — it is the 4-way *consolidation* classifier (ADD/UPDATE/NOOP/DELETE). **And the 34.4% mechanical figure was an audit regex, not shipped code.** The predicate is **buildable from the same signals** and those signals are **proven to fire on real data** — but the code is new work.

### 12.2a Where the rule lives — ALREADY DECIDED (adoption gate, contradiction test run 2026-09-23)

**The question posed was: does this rule belong in the pack (declared) or in the write path (computed)?** **The question is falsely binary, and it was already answered in this repository. It should not have been asked.**

**⚠️ A decision is recorded — `#1026` §3, verbatim:**
> *"the brief already is the seam; make it explicit that **NO domain behavior lives in the engine code — everything domain-specific comes from the compiled pack config (the engine is pack-agnostic)**"*

**And `#4899`'s acceptance criteria name the same rule for the adjacent gate:**
> *"The rule set is **derived from pack `memory_granularity`**, not hardcoded in the extractor — **the packs stay the source of policy**"* … *"Derive the rules from there **so the gate cannot drift from declared policy**."*

**The field converges on the same split** (so this is adoption, not a judgement call):
> *"Separate **what** from **how**: configuration declares **what** the pipeline does; code implements **how** it does it. Keep them apart."* … *"if it needs `if`/`else` logic, **it belongs in code**."*
>
> *"The extraction schema should be a **configuration artifact, stored per tenant and read at runtime** … validation driven by **per-tenant rules rather than shared globally**."*

**⇒ ADOPTED — DECIDED 2026-09-23. The shape is BOTH, and each half is settled:**

| half | where | why |
|---|---|---|
| **The POLICY** — which text is worth embedding | **the pack** (`memory_granularity`, plus per-kind overrides) | domain behaviour; the pack must stay the source of policy |
| **The PREDICATE** — how "identifier-only" is actually computed | **the engine** — one shared, testable function | mechanism, shared across every pack; `if`/`else` logic belongs in code |

**⚠️ Correction to an earlier note in this section.** An earlier draft recommended **"computed, because a class-level rule fails"**, citing the owner's meeting counterexample as the reason to move the rule OUT of the pack. **That is the wrong conclusion:** the counterexample shows **the unit must be narrower than a class**, not that the policy leaves the pack. **A meeting is a KIND** (`pointKind`/`objectKind`) — so the pack declares it per kind:

```yaml
embedding:
  policy: derive-from-memory_granularity   # ONE shared predicate, engine-implemented
  overrides:
    - kind: dev:meeting    embed: always    # an authoritative calendar title carries meaning
    - kind: dev:commit     embed: never     # a hash is pure identifier
```

**⇒ The meeting case is solved BY the pack, not by escaping it.** It forces the pack to be **per-kind**, which it already is (`pointKinds`/`objectKinds`).

#### ⭐ And the decisive finding: the policy is ALREADY WRITTEN DOWN — as prose, unenforced

**`packs/dev/manifest.yaml` → `memory_granularity` already declares ephemeral as:**
> *"issue/PR numbers, CI status, test counts, commit hashes, tool workarounds, sprint mechanics"*

**That is close to "identifier-only text", but NOT the same predicate — and the difference is load-bearing.** ⚠️ **`memory_granularity` is a RETENTION policy (what not to KEEP), not a MEANING predicate (what not to EMBED).** Two of its listed members are plainly **semantic**: *"tool workarounds"* and *"sprint mechanics"*. By §12.2's own test — *"does its embedded text carry meaning that its identifiers do not?"* — a **workaround would earn a vector**. And §7 separately requires decision-relevant operational values to be **retained** and associable. **⇒ The declaration is the right SOURCE, but the `EMBED` gate cannot be `memory_granularity` evaluated twice: it must state how it differs for the non-identifier members of the ephemeral list.**

> **⇒ The embedding gate is NOT a new decision, a new slot, or a new pack clause. It is the SAME missing enforcement as `#1026`'s `valueGate` / `#4899`'s `DISCARD` — the same declaration, applied at a second point.**

| gate | question it answers | lands in | owner issue |
|---|---|---|---|
| **DISCARD** | does the row **exist**? | `classify_consolidation` | **`#4899`** (Phase 1, flag-gated) |
| **EMBED** | does the row **carry a vector**? | the embedding submission step | **new — same rule source** |

**⇒ The work order is: enforce the declaration that already exists, at both points.** `#4899` Phase 1 builds the rule set from `memory_granularity`; **the embedding gate reuses that same rule set** rather than re-deriving it. **Two gates, one source of policy, zero new declarations.**

**✅ DECIDED 2026-09-23 — the two gates SHARE ONE RULE SET and enforce at TWO POINTS. Adopted, not escalated (contradiction test run, field converges).**

The tension was posed as *"share one rule set, or let them diverge?"* — **and the research shows those are not alternatives; they operate at different levels:**

> *"Classify data before it enters the pipeline and **preserve that classification** through copies and derived datasets"* — i.e. **one source of truth, controls applied at each step.**
>
> *"Classification metadata produced by a classification pipeline and then used later **for use in applying policy**"* — **one classification feeding multiple downstream policy decisions.**
>
> *"Reusing **rule intent** is useful, but the actual gates should be **staged and purpose-specific**"* — NVIDIA's pipeline separates heuristic filtering, dedup, model-based quality filtering and PII redaction into **distinct stages**.
>
> *"Divide work into filters with **non-overlapping responsibilities** and exact handoffs."* (Pipes and Filters pattern)
>
> *"A **baseline guardrail ruleset** and **separate enforcement points**"* — shared intent, distinct gates.

**⇒ The adopted shape:**

| level | decision |
|---|---|
| **The RULE SET — one, shared** | derived from pack `memory_granularity` (per `#4899`), **so it cannot drift from declared policy** |
| **The GATES — two, purpose-specific** | `DISCARD` (*does the row exist?*) and `EMBED` (*does the row get a vector?*), each **evaluating the same rule set for its own question** |

**✅ Why the contradiction test passes, and this is the reason it was adopted rather than escalated:** **`#4899`'s own criterion already IS the shared-rule-set requirement** — *"the rule set is derived from pack `memory_granularity` … the packs stay the source of policy"* and *"so the gate cannot drift from declared policy."* A second, independently-derived rule set for `EMBED` would be **exactly the drift that criterion exists to prevent.** Nothing recorded calls for divergence — the *"worth keeping but not worth embedding"* observation (a merge `Event`) is an argument that the **evaluation is parameterised by the question**, not that there are two rule sets.

**⚠️ So the design implication is concrete:** the rule set is **one declared artifact**, and the gate is **a function of `(rule_set, question)`** — not two functions over two rule sets. **The `Event` case is proof of why the parameter is necessary, not proof that the sets differ.**

### 12.2b The levers, ranked — and one earlier recommendation is WITHDRAWN

| # | lever | what it does | cost |
|---|---|---|---|
| **1** | **Don't embed identifier-only text** (§12.2's rule) | removes vectors no semantic query could want — **and improves precision**, since an identifier-only row is a false candidate generator | low, **measurable** |
| **2** | **Do not store what we do not need** (§11.5 fan-out cap; the extractor work) | fewer nodes ⇒ fewer vectors ⇒ less RAM rent, **with no engine change** | the extractor work we are already doing |
| **3** | **Density** — pack more graphs onto one instance (≤75% of its RAM) | the only lever that lowers **per-tenant** cost without dropping anything | ops, not code |
| **4** | ⚠️ **Partition the vector index per tenant** | **PARTIALLY RESTORED — see §12.1a.** The withdrawal conflated **two** mechanisms. **Declarative `PARTITION BY tenant` is FLAT** (measured 6.4 → 1.8 → 6.9 ms at T=10/100/500) and **is available**; it is the **partial-index-per-tenant** pattern that degrades (6.9 → 12.5 → 83.3 ms) — a per-relation planner cost, with no lock exhaustion observed at any count tested. **The decision is neither — it is the size-earned conditional index** (§12.1a). | low, **measured** |
| **5** | **Self-host the engine** (SSPLv1, free) | removes the `$0.10/GB-hour` rent entirely; same engine, no rewrite | ops work — **DEFERRED by owner decision until there are users** |
| **6** | **Quantization** — a vector-store option, not a graph one | shrinks the vector bytes; the graph term is untouched | **not applicable to hosted FalkorDB**; it becomes available with the vectors, if V1 moves them |

**⇒ Ranked honestly: levers 1 and 2 are the ones we can pull now, and they are the same work — write less, and write things worth keeping.** Lever 3 is the ops dial. **Lever 5 is the largest remaining ENGINE-side cost lever — the central cost move is the record/cache split itself (§2.0) — and it is deliberately deferred.**

**⚠️ Lever 4 is PARTIALLY RESTORED, and the withdrawal stays visible on purpose.** It was published in an earlier draft as *"none — pure win"*, then withdrawn wholesale when a study showed a per-relation planning cost. **Both readings were wrong about the mechanism:** that cost reaches only the **partial-index-per-tenant** form, and **declarative `PARTITION BY tenant` measures FLAT to T=500** (§12.1a). **The decision is still neither — it is the size-earned conditional index.**

**⭐ And the lesson worth keeping is the one §12.1a states: the withdrawal mis-attributed ONE mechanism to BOTH branches.** It is a query-planner cost on the number of indexes **per relation** — not a demonstrated ceiling — so *"a cost analysis would never have found it"* was true of the partial-index form **and equally true of the conclusion drawn about the other one**. That is the argument for measuring before recommending, and it is why §12.1's unmeasured latency claim was so costly.

### 12.2c ⭐ What earns a VECTOR and what earns a TRAVERSAL — the research answer (2026-09-24)

**Owner, 2026-09-24:** *"the core idea of what we do is being able to understand how an Object relates to events, subjects and points so we can not only have 'what is' but why and the context around it (who, when). So I think storing objects in the vector store is very important but then surprised we're not already doing it, although maybe this is more about enabling a search modality as they can be in the graph and we can do graph traversals without vectorising an Object, right?"*

**⭐⭐ The owner's parenthetical is exactly right, and it is the load-bearing distinction. The research settles both halves.**

**Half 1 — entities ARE vectorised in every comparable system.** *(HIGH — 6+ independent systems.)*

| system | entities vectorised? | what the vector is FOR |
|---|---|---|
| Microsoft GraphRAG | ✅ entity *description* | entities are **"access points into the knowledge graph"** — the entry point |
| Neo4j GraphRAG | ✅ name + description | **"find starting points in the graph, then follow relationships"** |
| Zep / Graphiti | ✅ `name_embedding` (1024-d, of the **name**) | **resolution** (dedup) **+ entry** — `NODE_DEDUP_COSINE_MIN_SCORE = 0.6`, top-15 candidates, then an LLM decides |
| HippoRAG | ✅ | picks the **Personalized PageRank seed nodes** |
| LightRAG | ✅ `entities_vdb` | local-mode similarity lookup |
| LlamaIndex PropertyGraphIndex | ✅ (default) | embeds entity/relationship text as retrieval nodes |

**⇒ The convergent pattern is unanimous: the entity vector is the ENTRY KEY (query → entity) and the RESOLUTION KEY (mention → existing entity). It is NEVER the reasoning mechanism.** Every system then **traverses** and pulls **raw text units / episodes** for the answer. **Nobody asks a vector *"how does this entity relate"* — you hold the node and walk.**

**Half 2 — the why/who/when question is TRAVERSAL, and we measured it.** Our walks cost **1.55–2.35 ms** (§12.1d). *"Which claims are about this entity, which event carries it, which source did it come from"* is a walk from a node we already hold. **No similarity is involved once the node is in hand.**

**⇒ The correct split for us:**

| job | mechanism | why |
|---|---|---|
| *"which SOURCES/CLAIMS are about X?"* | **vector** over claim/source text ✅ *(have it)* | recall — you do not know which node to start from |
| *"what relates to THIS entity — why, who, when?"* | **traversal** (`aboutObject`, `CONTAINS`, `extractedFrom`, `IMPL`/`NAND`) | **you already hold the node**; similarity adds nothing |
| *"which entity is this mention talking about?"* | **lexical (name + aliases) + a name vector** | resolution — the classic entity-linking job |
| *"which entity is LIKE this one?"* | **a vector over a DESCRIPTION** | the one job our Objects cannot serve today |

**⚠️ AND THE THING THAT MAKES OUR CASE DIFFERENT — measured, not asserted.**
Every system above embeds **name + description/summary**. Our `:Object` carries **neither**: `Object.description` → **0 rows**, `Object.summary` → **0 rows** (measured 2026-09-24). Its only text fields are `name` and `title`. **And 62.3% of those names are bare definite descriptions** — `"the plan doc"`, `"the timeout command"` — which **cluster tightly in vector space** (near-synonymous strings) and **block badly lexically**.

> **Taking *"objects must be vectorised"* literally would index 7,859 strings that are 62.3% low-information, with no description field, and buy near-zero discriminating power for BOTH jobs a vector could do** — while paying the dominant RAM cost.

**⭐ What we already have is the right shape:** `Object.name` **already has a FULLTEXT index**, as do `Event.subject`/`Event.name` (measured, §12.1b). **Lexical entity lookup is the resolution signal every system above pairs with the vector — and we have it.** What we lack is (a) the **description field** that would make an entity vector meaningful, and (b) a ruling on whether `:Object`/`:Event` embeddings should exist at all (`#4997`).

**⚠️ CORRECTION ACCEPTED (owner, 2026-09-24) — weak names are a SEQUENCING fact, not an argument against the vector.** The definite-description problem **is already an in-flight extractor workstream** (VET/CLASSIFY, the `DISCARD` path). An earlier draft of this section used it as a reason to doubt entity vectors; **that overstated it.** The correct reading:

| order | why |
|---|---|
| **1. the extractor improves entity text** | fewer, better entities — names stop being near-synonymous |
| **2. then the entity vector earns its keep** | it is worth embedding a real entity, not a definite description |

**⇒ The entity-vector work is DOWNSTREAM of the extractor work, not parallel to it — a reason to sequence, not a reason to drop.**

**⚠️ Also settled, because the distinction is easy to get wrong: *"embedding an entity"* means two unrelated things, and only one is deployed.**

- **(a) a TEXT embedding** of a name/description — a sentence-encoder output, found by cosine against a query string. **This is what every system above uses.**
- **(b) a KNOWLEDGE-GRAPH EMBEDDING (KGE)** — TransE/RotatE/ComplEx/node2vec/GNN, learned from topology, used to score unknown triples. **No surveyed production memory system uses one** — it optimises link prediction, not node lookup, **a disjoint objective. If we are ever told *"embed the graph"*, ask which of these is meant.**

**⚠️ Adversarial honesty:** the counter-case was **NOT** found — **no system reports adding entity embeddings and removing them**, and **no measured ablation isolates an entity vector's contribution to multi-hop answer quality**. **So Half 1 is strong framework consensus; Half 2 (traversal) is true by construction.** The defensible position: **an entity may carry a vector as an ENTRY/RESOLUTION key, budget-capped as a seed index — never as a resident corpus, and never as the reasoning mechanism.**

### 12.3 The tiers — and there are TWO, not three

**Owner proposed three tiers; the published architecture uses two, and the two-tier version is the one with measured results.**

| tier | what it holds | where it lives | when it is read |
|---|---|---|---|
| **Tier 0 — always** | **the vector index** + the hot rows it ranks | **RAM** | every semantic query |
| **Tier 1 — default answer** | **the summary layer** — narrative, TLDR epistemic, the `Object`/`Subject` neighbourhood, recent `Event`s | Postgres, cached | **by default** |
| **Tier 2 — escalation only** | **the raw log + the full epistemic depth** — every `Event`, the operators, the provenance spans | disk, cold | **only when Tier 1 is insufficient** |

**Why folding the owner's middle tier in is correct:** the owner's tiers 2 and 3 (*"TLDR epistemic/events related to an entity"* vs *"fuller data around an entity"*) are **the same tier at two depths**, and depth is exactly what a **sufficiency router** already controls — see §12.4. **A third tier adds a routing decision and a failure mode, not a capability.**

### 12.4 Escalate on SUFFICIENCY, not on query type — the load-bearing rule

**The obvious design is to classify the query ("simple → summary, complex → raw") and route on that. It is the design that fails.**

**Why: the write-before-query barrier.** Compression and placement decisions are made **at write time**, before anything knows what a future query will hinge on. So a query-type router is guessing at **a need it cannot observe** — and the published failure modes are exactly that guess going wrong: **router overhead**, **policy drift** (the learned router fetches from the wrong tier), **incorrect promotion** (an episodic fact promoted into a semantic rule).

**⇒ Route on a measured property of the ANSWER, not a predicted property of the question:**
> **"Does the evidence I already have actually answer this?"** — and escalate only when it does not.

**This is the same discipline `#2354` already records for corrections** (*"sweep never auto-resolves"*): **detect insufficiency, do not guess intent.** It is also cheaper — a sufficiency check runs on the **assembled evidence** (already in hand, one call), whereas a complexity classifier needs its own inference on every query.

### 12.5 The missing mechanism — the escalation must WRITE BACK

**Escalating to Tier 2 is where the cost is. If the answer is then discarded, the same escalation is paid again next time.**

**The published fix:** *"TierMem then writes back **verified findings as new summary units linked to their raw sources**."*

**⚠️ This is the mechanism we do not have, and it is the difference between a tiering scheme that gets cheaper over time and one that does not.** The published failure list names its absence directly as **"no attribution loop."** **⛔ The write-back must respect extractor §2.4.2: a finding written back to Tier 1 carries the SPAN, and the raw fact behind it stays immutable.** Anything else turns the summary tier into a system that quietly overwrites its own evidence.

### 12.6 The published architecture — `TierMem` (arXiv 2602.17913, 20 Feb 2026)

**The owner's design, published, with measured results — which means we adopt its specifics rather than invent them.**

| | |
|---|---|
| **Problem named** | the **write-before-query barrier** → **unverifiable omissions** (*"decisive constraints (e.g. allergies) may be dropped, leaving the agent unable to justify an answer with traceable evidence"*) |
| **Architecture** | **provenance-linked two-tier hierarchy**; answers from a **fast summary index by default**; escalates to an **immutable raw-log store** only when **summary evidence is insufficient** |
| **Framing** | retrieval as an **inference-time evidence allocation problem** — *answer with the cheapest SUFFICIENT evidence* |
| **⭐ Result (LoCoMo)** | **0.851 accuracy vs 0.873 raw-only** — **2.2 points** — at **54.1% fewer input tokens** and **60.7% less latency** |

**Reading the trade honestly:** the tiered path is **2.2 accuracy points worse** and **~2.5× cheaper**. That is the real price of tiering, and it is a good trade — but it is **not free**, and the 2.2 points are the number to beat or accept.

**Its shape matches ours already:** immutable raw log (extractor §2.4.2) · provenance link (the span) · summary ≠ evidence (extractor §2.4). **We are missing the router and the write-back.**

### 12.7 Failure modes to design against (adversarial pass — all published, none ours)

| failure | what it looks like here |
|---|---|
| **context collapse** | assembling so much that the reader cannot find the decisive fact |
| **compaction discontinuity** | S1's narrative loses what a later query needed — the `#3011` falsifier, seen from the write side |
| **structural blindness** | serving text where the query needed the **operator structure** — i.e. serving Tier 1 to a "why is it this way" question (extractor §2.4) |
| **no attribution loop** | escalating repeatedly and learning nothing (§12.5) |
| **router overhead** | the router costing more than the retrieval it routes. Published instances: **vector fragmentation** and **routing serialization** |
| **policy drift / incorrect promotion** | the router learning to fetch from the wrong tier |

**⚠️ Two HNSW-specific ones that are ours to avoid:**

- **HNSW does not reclaim space from deleted vectors in place** — tombstoned nodes persist until a full `REINDEX`. **Our derived layer is mutable by design (§3), so churn bloats the index.** Re-embedding at volume needs a `REINDEX` plan, not just deletes.
- **The query's operator must match the index's operator class**, or the planner **silently** falls back to a sequential scan. (The same class of failure as Hindsight's `fact_type` filter — *"~50× slower"* without a partial index.)

### 12.8 What this means for our design — the four things to do

1. **✅ DONE — M1 measured our own latency (§12.1d, 2026-09-24).** The whole retrieval layer is **single-digit ms**; the vector leg is **~3–5 ms**; a **full scan of all 7,859 `:Object` vectors is 9.26 ms**. The 96 ms vs 24 ms trade is invisible in that frame — **and that is now a measured fact about OUR loop, not an assumption.**
2. **Decide what gets an EMBEDDING, not what goes in the graph.** §12.1–12.2: **the vector class is the one that dominates the measured RAM attribute bytes** (§12.1's `Point` 50 MB / `Object` 13 MB / `Event` 9 MB). ⚠️ **Not because a "disk rule" has an exception — §11.7 corrects that framing; on FalkorDB the engine is in memory throughout (§2.1).** It is simply the largest class we can *choose* not to create. **This makes "fewer vectors" the extractor's highest-value cost lever.**
3. **Build the sufficiency router, not a complexity classifier** (§12.4) — and check it against `#2354`'s "never auto-resolve" discipline.
4. **Build the write-back** (§12.5) — otherwise escalation is a permanent tax rather than an investment.

**⚠️ And the discipline that keeps this honest: our own numbers for the cost TIERS still do not exist.** Every tier figure above is **published third-party** and must be re-measured on our data before any lever is committed. ✅ **What IS now measured is our own retrieval latency (§12.1d) and our own memory composition (§12.1) — those are no longer borrowed.** *(⚠️ The former "our 95.6 MB measurement", cited here, has been **withdrawn as wrong** — §12.1.)*

## 13. Issue map — what this document informs

**Purpose:** every section above exists to inform a filed issue. This table is the index, so a reader holding an issue can find the reasoning, and a reader holding a section can find the work it drives.

| section | issue(s) | what the issue takes from here |
|---|---|---|
| §1 the problem | `#4333` | the measured volume, and the true bytes per quota node. ⚠️ **Corrected 2026-09-23:** the earlier *"5.6 KB vs 1 KB, a 5.6× understatement"* divided **total instance RAM (140 MB)** by the **capped node count (25,000)** — but the numerator includes ~19,944 **quota-FREE** episodic turns, plus Events, Sessions, Documents, scaffolding and the full HNSW index. **`#4333` — the authority this row cites — estimates 2.5–4×.** The honest figure is **~3 KB per node (140 MB ÷ ~45k total nodes) ≈ ~3×**; 5.6 KB must be labelled *"total resident bytes per CAPPED node, including uncapped nodes"* |
| §2 mechanism | `#4333` | why the RAM/disk difference *is* the economics |
| §3 architecture | `#4333` | the physical split, **in the ontology's own vocabulary** — not a new layer |
| §3 what is already in code | **`#4240`** | **the hosted path does not journal unless `TORTOISE_EVENT_LOG_BASE_DIR` is set (`#4240` wired the per-graph journal) → a wiring gap, not a design gap** |
| §3 why this shape | `#3895` | the derived layer becomes **REBUILDABLE** — ⚠️ **NOT "disposable"**: §3 forbids that reading in bold, and `#3895` is a *restore*. "Disposable" is the label that would license dropping the only copy. It regenerates **from the truth layer**, and only because the journal carries the payload |
| §4 tenancy | `#3885` | **one project, tenant-scoped rows** — not one project per team |
| §5 cost | `#4333` · `#4614` | published rates; ~10× headroom at 1,000 users |
| §6 the split does NOT fix volume | **`#4899`** · `#1026` | **storage ~580× on the STORAGE line × selection ~10× (a target, not a measurement) — neither alone reaches 100×.** This is the section that stops a storage migration being sold as the fix |
| §7 state values | **`#2453`** (LANDED) · **`#4899`** (enforcement) · `#2820` | the carve-out, plus the DECIDED placement (owner, 2026-10-06): a state/numeric value is a **structural field on an entity**, with the embedding clause conditional on the §7 query-pattern measurement. ⚠️ **The former "`#1509` (E2, §9)" citation was WRONG** — `#1509` has no §9 and no E2; the placement recommendation there is a **comment** on `#1509` — now **SUPERSEDED by the PLACEMENT ACCEPTED decision (§7)** — and **E2 is `#1534`'s slot** (CLOSED). See §7's own corrected note |
| §7 caveat | **`#4889`** | ⚠️ **CORRECTED (owner, 2026-10-06).** The live graph is a **DOGFOOD instance** — an edge count there was never the right test, and the earlier *"0 edges"* framing is withdrawn. **The code and its tests exist** (`tortoise/subject_binding.py` and the `aboutSubject` write paths exist; `#4889` is CLOSED), and the **end-to-end check needs a capstone issue (not yet filed)**, not a caveat asserting a production count. |
| §8 granularity | `#1509` (E3) · `#4333` | **volume reduction may not come from fusing claims** — it bounds the whole document |
| §9.1 narrative placement | `#2281` | the narrative **is** the ingest-time-distilled layer that issue asks for |
| §9.2 PR / Object / Event | `#1844` | object-only sources; the anchor is a document, **never an event** |
| §10.1 relocation | `#4240` | the hot table stays the working set — which is the invariant this document rests on |
| §10.2 allowlist | — | a reusable correctness rule (no issue needed) |
| §12.1b unindexed embeddings | ⭐ **`#4997`** | **only `:Point` has a vector index; `Object`/`Event` full-scan and report `ok`; 19 MB stored unindexed** |
| §12.2c vector vs traversal | ⭐ **`#4997`** · `#2730` | **the entity vector is an ENTRY/RESOLUTION key, never the reasoning mechanism — and our Objects have no description to embed** |
| §9.4 source summary vector | — | **needs a ruling (V2); `Source` already carries `summary`, `topics`, `url`, `contentHash`** |
| §9.6 source versioning | — | **the version must be recorded on the extraction link as `sourceVersion`**; a version costs **three timestamps + a hash** (D30 keeps content out of the graph) — **bound it: windows and hashes only, never content copies** |
| §12.1a index shape | `#5090` · `#5331` | **the decision is the size-earned conditional index (C)** — not per-tenant partitioning, not a shared filtered index. The partition comparison is a measured *alternative*: `PARTITION BY` flat to T=500 (96.6 ms at T=2,100, unexplained), the partial-index form degrading to 83 ms at T=500 |
| §17 the five gaps | **`#7951`** (17.1) · **`#7952`** (17.2) · **`#7953`** (17.3) · **`#7954`** (17.4) · **`#7955`** (17.5) · **`#7967`** (the surface map) | what the record/cache split still needs: a write-path chokepoint, cache coherence + a completeness marker, partial-cache semantics, a two-store cost model, and a field-level placement table. **None is a new decision** — each follows from §2.0. ⚠️ **Corrected 2026-10-10 — this row previously read `#5089` (17.1) · `#5090` (17.5), which named the wrong owners:** **the routing half of §17.1 is `#7951`'s** (`#5089` owns the journal half — §17.1 says so itself), and **§17.5's placement ENUMERATION is `#7955`'s** (`#5090` is a *consumer* of the vector layer, not the enumeration). The five gaps are children of **`#7879`**; **`#7950`** is the capstone that verifies them; **`#7968`**/**`#7969`**/**`#7971`** carry the schema, the record projection and the cache projection, and **`#7970`** the read path. The integration-surface map every one of them cites is **`#7967`** → `docs/epics/2026-10-10-7879-postgres-record/01-test-design.md` |

**Not yet filed from this document (candidates, not decisions):**

- **`aboutObject` as a stored edge vs a derived join — the measurement is DONE (research brief, 2026-09-23) and the answer is SPLIT, which decides it.**

  **The primary search path does NOT read it.** `rg -n 'aboutObject' tortoise/retrieval.py tortoise/search_engine.py tortoise/session_reinjection.py` → **exit 1, zero matches**, case-insensitive. **Hindsight's premise holds for this path.**

  **It IS load-bearing elsewhere — four default-ON sites, two of them customer-callable:**

  | # | site | what it reads for | kind |
  |---|---|---|---|
  | 1 | `sdk.py:6397` — SDK **`belief_timeline`** (`mcp_server.py:3126` exposes it) | walks decision → Object named by topic | traversal |
  | 2 | `topic_summarization.py:174` — SDK **`topic_summarize`** | finds seed claims via Object | traversal |
  | 3 | `extractor_v2.py:1934` | the classifier's "same entity?" gate | traversal |
  | 4 | `mining.py:477` (`_temporal_wire`) | finds prior decisions on the same Object, then **mints a NAND** | traversal, **2-hop** |

  Plus `sdk.py:14744` (`_entity_key_expansion_pass`) — **OFF by default** (`entity_key_expansion: bool = False`, `sdk.py:13777`). And **23 test files assert the edge.**

  **⇒ The comparable's conclusion does NOT transfer.** Their justification was *"recall never touched them"* — **false here.** Deleting the edge would silently break two public surfaces.

  **But the structural door stays open:** a full-repo search found **zero** `aboutObject` edges carrying **any property** (`rg -n 'aboutObject \{'` → 0 matches / 1,842 files). Unlike Graphiti — whose *fact and validity window live on the edge* — **we lose nothing semantically by deriving later.**

  **⚠️ `#4240` is half-right, and the true half is the sharper argument.** Capture-path edges **are** journaled (`EntityLinked`, `#3664`; replayed by `projection/entities.py:785+`). **Indexer-path edges are NOT** - `_connect_issue_objects` (`sdk.py:20154`) calls `create_about_edge` with no journal write, and `test_index_restore.py:373-403` asserts `count(*) == 0` after rebuild. **So the finding is not "this edge saves no storage" but "this edge class is internally inconsistent about durability"** - a cleanup question, arguably more urgent than stored-vs-derived.

  **⚠️ The derived design is what CREATES a fan-out problem.** Hindsight's join needed a `LATERAL LIMIT per_entity_limit` (default **200**) *and* a timeout that **drops the entire entity arm**. A join on a 1,200-claim hub yields ~1,200 intermediate rows **per anchor**. **So the cap is the price of deriving — and also the guard rail for keeping the edge.** We have neither today.

  **What remains UNKNOWN, and would flip the recommendation:** whether `belief_timeline` / `topic_summarize` are **ever actually called** in production. If they are reachable-but-unused, Hindsight's premise is restored and deriving becomes correct.

  **✅ OWNER RULING 2026-09-23: KEEP the stored edges — and the stored-vs-derived question is DEFERRED to post-launch, not left open.** *"we haven't launched yet, so unanswerable. keep them for now, we optimise with users."* **The unknown above is unanswerable before launch — there is no usage to count.** So: **keep the stored edges; revisit once there are users and real call data.** ⚠️ **The deferral costs only storage — ~3 MB, per §11.1 — so deferring is genuinely cheap, and it is the right call: it buys the option back for the price of nothing.**
- **Fan-out caps** — nothing in our write path bounds how many edges one entity accumulates (`config/ci-surfaces.yml` at **123**, `durations map` at 111, `the admin-merge rail` at 70, `cal-trigger.py` at 64, `the plan doc` at 63). Hindsight caps at **200** and drops the arm under budget pressure. ✅ **ADOPTED — initial value 200** (owner, 2026-09-23). ⚠️ **§11.5 is the specific record;** an earlier draft of this bullet said *"adopted in principle; no value set yet"*, which **contradicted** it. For the record: it is a **guard rail, not an optimisation** — it binds nothing today (worst hub is 123) and is refined with real usage.
- ⭐ **NEW (2026-09-24) — the unindexed embeddings (V3, §12.1b).** **MEASURED ON THE LIVE GRAPH AND FILED: `#4997`.** `CALL db.indexes()` confirms **exactly one vector index — `Point.embedding`**; `Object`, `Event`, `Source`, `Subject`, `Document` have **none**. **12,657 stored vectors (`:Object` 7,859 + `:Event` 4,798), ≈19 MB, 13.5% of the graph, are paid for and unindexed** — and they full-scan at **16.96 ms vs 4.97 ms** while `leg_trace.reason` still reports **`ok`**. **The mechanism is a drift, not an oversight:** `#172` made the *query* label-generic; `_ensure_indexes()` still hardcodes `'Point'`. **Two readings, one ruling: stop storing them, or make index creation label-generic.** **This is the one candidate that is a *defect* rather than a design question.**

---

## 14. What this document does not decide

- **The vendor** — **Supabase Postgres** is the owner's recorded choice for the system of record (local default now, Supabase post-beta). ⚠️ **`pgvectorscale` availability on Supabase Postgres is now CONFIRMED UNAVAILABLE** (2026-09-23, three independent checks: the live extensions list carries `vector`, `pg_partman` and `pg_prewarm` but **no `vectorscale`**; two open feature requests **`#27474`**, **`#29095`**; and Supabase's own features page). **An earlier draft of this bullet said "unverified" while §2/§12.2b presented it as a settled blocker — that contradiction is now resolved in the blocker's favour.** **If a genuinely disk-resident index is ever required, S3 Vectors (GA Dec 2025, 2B vectors/index, ~100 ms) is the escape hatch — at the cost of a second system.**
- **Self-hosted FalkorDB** — free under SSPLv1. This is the problem with *FalkorDB Cloud pricing*, not FalkorDB. It must be ruled out on operations and scaling grounds, explicitly, rather than ignored.
- **Migration effort** — no estimate exists against the cost delta.

**✅ AND ONE ITEM LEFT THIS LIST on 2026-10-08: *"whether to drop a separate graph engine at all."*** It is **decided by §2.0** — the graph engine is **retained as a derived hot cache and DEMOTED, not dropped** (§14.2). The Postgres-native alternative (plain tables + `pgvector` + `tsvector`/`rum`; Apache AGE evaluated and rejected — unavailable on Supabase, buys nothing for a workload with no in-DB traversal, plus open silent-data-loss and property-index defects) survives as a **conditional future option should the cache ever be retired**, not as a live proposal. **⚠️ Earlier drafts filed it as undecided; a lane must not re-open it on that basis.**

### 14.1 ✅ Decisions — all six closed (owner, 2026-09-24)

**These were escalated per the AGENTS.md protocol rather than settled by the lane. They are now answered. Recorded here so no future lane re-opens them.**

| # | the question | ✅ ANSWER (owner, 2026-09-24) | what changed |
|---|---|---|---|
| **O1** | Is the journal allowed to be the authority? | ⭐ **Neither side was ever decided — and the conflict was mine.** `#2826` **A2** (the actual decision row, owner-required) **recommended the journal be authoritative**; `#2881` §7 recommended reversing it; `durability-posture.md` recorded the reversal as *"the rule"* and flagged *"the row is the owner's to answer."* **The two questions are different and both hold: DURABILITY = the store's backup (`#2881`, unchanged); REBUILDABILITY = the journal.** | **No reopen.** The wording is scoped, not overridden: *"the journal is the derived layer's rebuild source; it is not a durability mechanism."* **⚠️ The real work this exposes: `rebuild_all` performs an unconditional wipe + journal-only replay — i.e. the CODE already treats an incomplete log as the authority.** That is a defect, and it gets its own issue. **Embedding in the journal: yes — a re-embed is a re-run, not a replay** (§3). |
| **O2** | Per-item or per-batch target; what recall floor? | ⛔ **THE QUESTION IS WITHDRAWN — the owner rejected its framing.** *"we're not optimising stupidly for a number… this is not a corporate OKR setting exercise. We need to balance multiple things at each step of the pipeline and on each architecture decision."* | **Removed from this document.** The objective is **great recall and reasoning at an affordable cost**; the method is **manual step-by-step review** until a calibration set exists. **No volume target, no recall floor, no node count.** ⭐ **AMENDED 2026-09-30 — the owner narrowed WHAT this withdrew: a TARGET, not a MEASUREMENT.** *"Tracking how many facts are useful is good data to have… having that measurement is good"*, with the goals explicitly deferred: *"we can decide what goals we optimise for later."* **⇒ What O2 withdrew is a number to optimise *toward*; measuring how many durable claims we kept is not such a number and survives it.** §6.1 and §15 are corrected to match. ⚠️ **No target, floor value or node count is set by this amendment** — only the measurement is re-affirmed. |
| **O3** | Which outcome word does the gate emit? | ✅ **ADOPTED — use the declared word (`DISCARD`), matching the engine's existing classifier.** | `#4899`'s text is corrected to the declared vocabulary. |
| **O4** | Can near-duplicates be merged? | ✅ **YES — the owner authorised it, with Jev as arbiter over the claim plus narrative/raw data.** *"yes we can merge near duplicate claims. maybe we can have jev with the claim and the narrative/raw data/both arbiter that"* | ⛔ **This SUPERSEDES the `OVERRIDES:` ruling on `#4899`** (*"never merging two claims into one"*). **The new ruling is recorded on `#4899`, replacing the old — the owner overriding their own earlier ruling, which is the only valid way to reverse one.** ⚠️ **Shaped by evidence (§16.3): a HIGH merge bar, and merge only when nothing distinguishing is lost — never across differing numbers, names, negations or conditions (the list has since grown; `EXTRACTOR-V4-ARCHITECTURE.md` §16.4 carries the current classes).** Practical thresholds cluster at ~0.95. |
| **O5** | Sample first, or make it deterministic? | ✅ **Neither as an either/or — draft and iterate.** *"we draft something (a prompt, a step of the extraction workflow, etc) and run it and see the result then refine and run again, until good."* | **The method is: draft → run → look → refine → repeat.** Recorded as the working method for every step. |
| **O6** | Which layer owns `Document`? | ✅ **Resolved, and better than either option: a document is a SOURCE (§9.3).** *"I am suggesting making them sources so our entity layer can be extracted from them…"* | **`:Document` folds into `:Source`; its content leaves the graph for raw text in Postgres (§3, §9.1); liveness is a READ of the entities, not a stored field.** ⚠️ **My earlier "Document sits in two layers" P0 was WRONG** (`ONTOLOGY.md` §4.4: one label, conceptual subclass). ⚠️ **Cost of the change: NOT zero.** 0 nodes makes the *data* migration trivial, but the label is **written, read and quota-metered** (`quota.py:524`, `#1726`) and `ONTOLOGY.md` §4.4 still declares it — **the code + ontology change is the work (filed: `#5013`).** |

### 14.2 ⭐ The engine decision — AMENDED 2026-10-08: the engine stays, its ROLE changes

**Owner, 2026-09-24:** *"for now we can keep FalkorDB hosted and then we figure out further optimisation."*
**Owner, 2026-10-08 (§2.0):** *"we do hybrid now, data goes to postgres and it's loaded to hot (falkor); for now we keep the limit in falkor high…"*

**What is UNCHANGED:** **FalkorDB stays hosted, and there is still no engine rewrite.** The 2026-09-24 decision was about the **engine and its host**, and the 2026-10-08 ruling disturbs neither.

**What changed, and it is the ROLE:**

- **Then:** FalkorDB was **the store** — the only durable home of the reasoning layer — with the Postgres target deferred until *"there are real users and a measured footprint."*
- **Now:** **Postgres is the system of record and FalkorDB is a derived hot cache** (§2.0, §3). **The engine is not replaced; it is DEMOTED.**
- **⚠️ And the deferred TRIGGER was inverted.** *"Revisit when there are real users"* is the **most expensive** moment to move a store, because migration cost scales with **customer count**. The 2026-10-08 ruling moves the trigger to **before the first customer** — and that is the whole reason for doing it now.
- **⚠️ The line *"Do NOT re-open this as 'shall we migrate to Postgres?'"* is RETIRED.** It was right under the 2026-09-24 decision; the owner has since reopened it and answered it. **§3's table already named Postgres for the derived layer — so this is the architecture made operational, not reversed.**
- **What is genuinely deferred:** nothing about *which store records what*. What remains a **cache-tier ops question** is **self-hosting** (SSPLv1, free) — a cost lever, **not a rescue** (§12.2b lever 5, §15 M4).

### 14.3 ⚠️ Open — raised 2026-09-24, NOT decided

**⚠️ AMENDED 2026-10-08 — the hybrid makes V1 more urgent, not less.** Under the 2026-09-24 decision the graph was the store, so moving the vector index was an optimisation. **Under the hybrid, FalkorDB is a CACHE, so every byte it holds is rent on something Postgres already stores** — which is the definition of the waste the split exists to remove. **V1 is still the owner's call, and it is now the *first* cache-tier question rather than "the month-6 answer".**

| # | the question | why it is not settled | what turns on it |
|---|---|---|---|
| **V1** | **Should the vector index live in FalkorDB at all?** (§12.1c) | It changes **what the graph holds** — a real change to the retrieval path, not a tune. Owner raised it; owner decides. **The latency half is now measured (§12.1d) — 1.5–5 ms — so the question is purely cost.** | **≈50 MB of stored vectors + a 45 MB index total, on 141 MB**, for an estimated **~10–15 ms**. **The first cache-tier question (§14.2).** |
| **V2** | **Does a `Source` get a summary vector?** (§9.4) | Buys exactly one query class (*"which source is about X"*). Evidence is practice, not measurement. | **~4.4 MB** — and **free** if V1 lands. |
| **V3** | ⚠️ **The unindexed-embeddings defect** (§12.1b) | **MEASURED AND FILED 2026-09-24 → `#4997`. REFRAMED: this and V1 are the SAME decision** (owner, 2026-09-24) — *"isn't the research pointing us to create those vectors but keep them in supabase?"* **Yes: keep the vectors, make them searchable, put them where RAM is cheap.** | **Rides on V1.** |
| **V4** | ⚠️ **The silent `ok` — the trace cannot tell an index hit from a full scan** | **FILED → `#4999`.** `run_vector_query` records `degraded=False, reason="ok"` on BOTH the indexed path (4.97 ms) and the brute-force full scan (16.96 ms). **V1-INDEPENDENT** — fix it wherever the vectors end up. | **Correctness of the observability layer.** At month-6 volume a scan is ~300–500 ms with the trace still saying `ok`. |

---

## 15. The measurement plan — what is actually worth measuring

**⚠️ CUT HARD 2026-09-24.** An earlier version listed seven measurements, several invented to defend numbers this document has since withdrawn. **A measurement is only worth listing if a decision turns on it.** ⭐ **M6 was added 2026-09-30 under that rule: the owner re-affirmed that the retention measurement is wanted (a decision turns on it — §14.1 O2, AMENDED), while the goal it would be compared against stays deferred.**

| id | question | why it matters | status |
|---|---|---|---|
| **M1** | **What is OUR query latency, warm?** (`EXPLAIN (ANALYZE)` + timing on the live graph) | **Every latency figure here is someone else's.** A one-liner that retires the entire §12.1 debate. | ✅ **DONE 2026-09-24 — see §12.1d. Answer: 1.5–5 ms; latency is a non-issue.** |
| **M2** | **What is our real bill?** — the actual FalkorDB invoice against the $9 → $19 budget, at month-6 footprint | the only cost question asked, and §5/§6 are arithmetic on published rates, not our invoice | needed |
| **M3** | **How much of the 140 MB is junk?** — re-measure after the extractor work lands | tells us how much of the CACHE's footprint is junk — i.e. cache-tier sizing and whether self-hosting (M4) pays. **It does not re-open the engine choice** (§2.0) | after extractor work |
| **M4** | **Does self-hosting beat Cloud at the CACHE tier's footprint?** — price a VM holding N tenants at ≤75% RAM | a **cache-tier ops** lever — **no longer *"the named revisit lever"*** (§14.2 amended): it moves a cache, it does not move the record | when there are users |
| **M5** | ⭐ **Where should the vector index live?** — our query latency with the index resident, against a real `GRAPH.MEMORY USAGE` read | **decides V1 (§12.1c) — the first cache-tier question (§14.2)** | **M1 done (§12.1d); `GRAPH.MEMORY USAGE` read (§12.1b). V1 is now a decision, not a measurement gap.** |
| **M6** | ⭐ **How many durable claims did we keep?** — the share of the candidate facts the pipeline saw that survived as claims | **The guard §6.1 requires: without it, a volume objective can be satisfied by writing nothing.** The owner re-affirmed **2026-09-30** that this measurement is wanted (§14.1 **O2**, AMENDED) — **only the goal is deferred.** | **needed — the denominator must be stated first** |

**WITHDRAWN (recorded so they are not re-invented):** the cold-index p95/p99 study (**premise was vendor marketing**), the quantization-recall study (**no engine to apply it to**), the 1,000-partition planning study (**§12.1a answers it: the PARTIAL-index form degrades — 83 ms at T=500 — while declarative `PARTITION BY` is flat to T=500; neither is the decision, which is the size-earned conditional index**), and the narrative-vs-raw A/B (**`#3011` is already pre-registered; do not duplicate it**).

---

## 16. Research outcomes (2026-09-24) — what was checked, and what it overturned

**Three research passes were run for this decision** (FalkorDB memory accounting; the idle-vs-active cost model; the latency question). **They are recorded here because two of them overturned claims this document had already published, and a future lane must be able to see which version is current.**

### 16.1 FalkorDB memory accounting — the cost model, settled

| finding | confidence | consequence for us |
|---|---|---|
| Cloud bills **provisioned instance RAM** ($0.10/GB-hour ≈ **$73/GB/month**), not dataset size | HIGH | cost is a function of the machine, not the data |
| The **whole graph must be in RAM**; no spill-to-disk; a graph may use ≤ **75% of instance RAM** | HIGH | there is no cheap tier inside one instance |
| ⛔ **No per-graph eviction on Cloud.** The only documented removal is `GRAPH.DELETE` (**permanent, no undo**) | HIGH | **an idle tenant costs exactly what an active one costs** |
| Per-graph LRU offload **exists — `falkordbe.idle-threshold-ms`, `GRAPH.LOAD` from `/data/offload`, surviving restart — but ONLY in FalkorDB Enterprise (self-managed)** | HIGH | ⭐ **the single most valuable thing to confirm with the vendor, and the strongest argument for self-hosting later** |
| `GRAPH.MEMORY USAGE` is a **sampling ESTIMATE** (`SAMPLES`, default 100) with a real field breakdown; excludes per-graph/Redis overhead | HIGH | **our 140 MB is an estimate — quote it as one** |
| Cloud pricing is **$73/GB/mo**; **self-hosting is free under SSPLv1** | HIGH | the deferred lever |

**⇒ Overturned:** every claim in earlier drafts built on *"an idle tenant is cheaper"*. **On our stack it is not, and cannot be.**

### 16.2 The latency question — the claim was withdrawn, not softened

**Owner's challenge, 2026-09-24:** *"is the 300ms a hard ceiling? where does it come from? and why would supabase queries be so slow >500ms?"*

**The answer: it is not a ceiling, and the numbers were not measurements.**

| what the document said | what is true |
|---|---|
| *"20–50× over every published latency budget"* | The budgets are **vendor self-report** — Zep's own marketing (155 ms p95, no methodology) and an **asserted table** in a blog |
| *"10,500 ms cold"* | A **billion-vector AWS deployment**. Ours is 140 MB. |
| *"96 ms vs 24 ms"* | **OpenSearch's disk mode** — a different engine |
| *"Nielsen 0.1s/1s/10s"* | **Human-facing UI** thresholds; applying them to a pipeline step is **not established** |
| *"retrieval dominates the wait"* | The **only published measurement** (Mem0): search p50 **148 ms**, whole turn p50 **708 ms** → retrieval is **~20–25%**, not dominant |
| *"a 41% share"* | From a **1 M–100 M chunk** datastore — a **scale artifact**, not a law |

⭐ **And the competitor post the budget was drawn from says the opposite of what was quoted:** *"there is no universal 100ms or 200ms requirement… Set that target for the interaction you are building."* **The caveat was dropped when the number was taken.**

**⚠️ The generalisable lesson, and it is the reason this section exists: a vendor's performance claim was promoted to a constraint, and an architecture was argued on it.** *"Cheapest first"*, *"partition per tenant — pure win"*, and *"cold reads cannot be user-facing"* all came from the same failure mode. **The gate is: does a measurement exist, and is it of OUR workload?** ⚠️ **(At the time this was written, §12.1's gap was that we had never timed our own query. ✅ THAT GAP IS NOW CLOSED — M1 measured it on 2026-09-24, §12.1d: our whole retrieval layer is single-digit ms. The question below was therefore answered by measurement, which is exactly what this section asks for.)**

### 16.3 Noise and quality — the belief is supported, the causal chain is not proven

**The owner's position, recorded:** *"less noise is good, move on."* **This is the document's position too, and it is not argued further.**

For the record, since two members of the design rest on it:

- **Supported (HIGH):** distractors measurably hurt retrieval accuracy; a Microsoft consolidation study reports **97.2% retention precision at 58% store reduction** with **+13.3 pp preference recall**.
- ⭐ **The mega-hub finding (GAAMA, 2026):** entity nodes accumulate hundreds of edges and *"high-degree hubs… **dilute retrieval precision**"* — corroborated by Elastic (prune high-cardinality hubs) and degree-bias research. **This is the evidence behind the fan-out cap (§11.5), and it is about edge QUALITY, not volume.**
- **Our definite-description problem is textbook:** mainstream NLP treats definite descriptions as **mentions**, not entities — *resolve or drop*, never *mint a node*. Our *"the timeout command"* is a non-referential mention promoted to a node.
- ⚠️ **NOT established:** any study linking **junk entity-node count** to worse graph-edge quality. **That step is our inference.** Recorded so it is not cited as a finding.
- ⚠️ **Counter-evidence that shapes O4:** over-aggressive consolidation *"destroys specific details needed for factual QA"*; **false merges cost more than kept near-duplicates**; practical thresholds cluster at **~0.95**. **Hence O4's conservative bar.**

---

## 17. The five gaps the hybrid ruling requires — and that this document did not cover

**⚠️ Added 2026-10-08 (`#7869`).** The 2026-10-08 ruling (§2.0) makes **Postgres the system of record and FalkorDB a derived cache**. §3 states the shape; **these five are consequences the document does not yet carry.** None is a new decision — each follows from the record/cache split.

### 17.1 The write-path rule — unstated, and unenforced

**§3 already states the rule for the derived TABLES — *"Writes go to the truth first, then project"* (`:308`) — but it does not extend it to the CACHE, and nothing enforces it anywhere. The REQUIREMENT, stated as a rule:** **every write goes to the truth store first, and the cache is projected from it.** The rule is not optional, and its reason is definitional:

> **A derived cache that is also written to directly is not derived.**

**Measured 2026-10-08** (method: a regex scan for mutating Cypher verbs over `tortoise/**/*.py`): **the write surface has NO chokepoint — 387 mutating `.query(` sites across 211 functions in 32 files** (`SET` 176 · `MERGE` 119 · `CREATE` 55 · `DETACH DELETE` 37; the top-20 functions hold only **125/387 = 32%**, and **135 functions have exactly one**). **⇒ "Point the write path at Postgres" is not one change** — and until the rule is stated and a chokepoint exists, the cache is written to directly, and is therefore **not derived**. **Where the work lives:** `#5089` carries the journal half; the routing half is §3's invariant.

### 17.2 Cache coherence — and a COMPLETENESS MARKER

**§3 covers ONE divergence surface** — journal ↔ projection, with five mechanisms and the measured status *"(1) absent · (2) absent · (3) count-only · (4) present and guarded · (5) absent."* **The hybrid adds a SECOND: Postgres (truth) ↔ FalkorDB (cache).** The document has no mechanism for it.

**The failure mode is specific, and it is why "add a fallback" is not enough:**

> **A fully-populated cache cannot reveal its own coherency bugs.** A wrong answer and a right answer look identical — both are `200`. A published KB→Elasticsearch desync ran **9 days** undetected *(industry write-up, 2026-10-08 research pass — carried as an illustration, not independently reproduced)*.

**Two requirements follow, and neither is in the document:**

1. **Continuous reconciliation** — `truth == cache` must be *checkable*, not assumed. **Deferring the fallback is safe only if the divergence is OBSERVED**, not merely recoverable.
2. **⭐ A COMPLETENESS MARKER — the load-bearing one.** A read for a node **outside the known-hot set** must return an explicit **`not-hot`**, never an empty result. **An empty result is indistinguishable from *"no such node"*** — so without the marker the cache silently answers *"nothing"* to a question it cannot see the answer to.

### 17.3 Partial-cache semantics — a residency miss is not an insufficiency

**§12.3's tier table assumes each tier is COMPLETE.** Under the hybrid, **FalkorDB is partial by design** — the limit is held high precisely so this is **deferred**, not absent. §12.4's sufficiency router is the seed, but it routes on **answer depth**; it is not a residency router.

**The two events are different and need different responses:** *"the evidence I hold does not answer this"* (escalate a tier) versus *"this node is not in the cache"* (fetch from the truth store). **Collapsing them either escalates unnecessarily or — worse — treats a cache miss as a negative finding.**

### 17.4 A two-store cost model — the existing tables price ONE store

**§5's cost table and §6's multiplier both model a single store.** The hybrid pays **both**: Postgres compute + disk **and** FalkorDB RAM. §6 concedes the gap in its own words — *"the blended total-cost figure is uncomputed; it needs the compute and IOPS lines, and neither has a measurement yet."*

**This matters because the split's whole justification is arithmetic.** The claim is not *"Postgres is cheaper"* but *"bytes that are never read again stop renting RAM."* **That claim has no table yet.**

### 17.5 A field-level placement table — 9 derived/volatile property names on the sampled surface

**§3's per-write test IS the mechanism** — *"if the journal does not carry it, it is not derived — it is PRIMARY, and belongs in a truth-layer table"* — and §7 moves state values onto entity fields. **What is missing is the enumeration.**

**Measured 2026-10-08** (10,000 `Point`s; **87 distinct property names** on that sample — itself truncated by FalkorDB's silent 10,000-row cap): **9 of those 87 property names are DERIVED or VOLATILE** and must not be in the system of record — `ep_alpha` (4,604 occurrences) · `ep_beta` (4,604) · `ep_dirty` (4,840) · `ep_dirty_at` (4,838) · `confidence` (1,125) · `posterior_alpha`/`posterior_beta` (848) · `baseline_set` (4,230) · `baseline_source` (4,217) — **29,306 occurrences across the 10,000 sampled points.** ⚠️ **State it as *"9 of 87 property names on the sample"*. A percentage is NOT quotable here:** the denominator that would make one meaningful — total property occurrences over all points — was not measured, and an occurrence share taken from a 10,000-row-truncated sample would repeat exactly the denominator error §1 warns about. **These properties are the cache's, not the record's** — and the ontology already says so (§2: confidence *"is derived at read time … **never stored independently**"*).

**⚠️ A related measured finding the placement table must resolve:** **two incompatible id formats exist on the live graph** — **8,700 `<hex>-<hex>` and 1,000 `p-NNNNNNN`** (plus ~30 `EV-*` strings used as ids). **A shared canonical id does not exist**, and a two-store design needs one.
