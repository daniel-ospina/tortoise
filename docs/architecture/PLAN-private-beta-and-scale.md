# Plan — private beta now, scale-ready by design (diagnosis → two parallel tracks → gates)

**Date:** 2026-11-21 · **Supersedes ad-hoc next-step lists.**

**Inputs:** this thread's measurements · the 2026-11-21 session handoff — an **external document, not in this repo**; `SCALING-ROADMAP.md` §6 restates each of its claims so the reconciliation stands alone · the
competitor research (`SCALING-ROADMAP.md` §1–§2.5) · trackers `#11081` (release), `#13989` (storage policy).

> ⛔ **This is a roadmap, not an incident queue.** Two tracks run **in parallel**: fixing what is broken now, and building the
> infrastructure the destination needs. Cheap things land first in both; nothing in the scaling track waits for permission
> that is really just "we don't need it yet" — I am the only dogfooder today, which is exactly when the free prep is cheapest.

---

## 1. The goal, stated once

- **Product (private beta, owner 2026-11-20):** storage migration done enough that new writes never need re-migrating ·
  extractor v4 verified · the approved surface **answered, not merely counted**: 22/27 tools and 27/40 SDK methods answered, 12 SDK
  methods `UNMEASURED` — the capstone's own recorded verdict is **NOT 27/40** (`docs/evidence/11609-surface-capstone/`) · **and the
  product works: onboarding, writing, search**.
- **Demonstration:** session A states a fact, session B contradicts it, the contradiction is **surfaced automatically**.
- **Destination (not this release):** a service that stays responsive as tenants and corpus grow, with the expensive work
  batched, queued and replicated — the architecture the comparables already run.

---

## 2. Diagnosis — seven mechanisms, and how they compound

Each has the evidence, a confidence, and **what would disprove it**.

| # | mechanism | evidence | conf. |
|---|---|---|---|
| **D1** | **Batchable work runs inline on the read path.** Every search embeds the query inside the request: `embed_encode` **2,078–4,003 ms** for one query. Batched: **0.36 s/item** (32 in 11.6 s vs 8 sequential in 15.2 s). | stage attribution, production | HIGH |
| **D2** | **Legs run sequentially, so latency is the sum.** `pre_handler 5,996 · embed_encode 4,003 · retrieval 1,199 · fusion 0.2 · ep 156`. Hindsight: *"latency is determined by the slowest parallel branch rather than the sum"*. | stage attribution vs competitor design | HIGH |
| **D3** | **Live session-capture traffic competes with reads.** 9-second log window: **6 × `POST /v1/sessions`, all 429** (≈36/min). The writer is the **pi capture extension inside agent sessions** — this session's own verifier run printed `[tortoise-capture] spool flush … paused 218 (budget; continues in the background)`. So the traffic is ours, continuous, and rate-limited rather than stopped. | production logs + the capture extension's own output | HIGH |
| **D4** | **One worker, one process.** Single uvicorn worker; `TORTOISE_MULTI_WORKER=1` is gated by `worker_topology.py::UNLANDED_GUARDS`, which holds **nine rows, one per holder** — the file's own docstring warns that collapsing them is how a single row gets read as "covered". The three most quoted are duplicate daemons filing issues N times, in-memory OAuth CSRF 404s at N≥2, and per-process index jobs; the rest include **rate-limit buckets × N** and the **model pre-warm at ~N× RSS on a 4096 MB box** — the ledger's row says ~85 s, while the tree's own measurement is a **≈196 s COLD load** (`embeddings.py:885-910`: 156.5 s of imports + 39.3 s construct) — both of which change the real cost of enabling workers. | `worker_topology.py::UNLANDED_GUARDS` | HIGH |
| **D5** | **Storage pressure converts slow into refused.** Graph hit **82 % of 1 GB**; org allowance **cap 102 MB / hard stop 153 MB** refused writes; a 142 MB retirement restored them. The bounded projection is designed (`#13989`) but **not armed** (`#13103`). | measured today | HIGH |
| **D6** | **Operational fragility mimics product failure.** Deploy **queued 8 min** behind runners while the site 503'd; a manual size change that did not survive a deploy (fixed by `#14156`); an alert step reporting `issue search failed — the finding may be UNRECORDED`. | observed today | MED |
| **D7** | **Memory accumulates contradictions without resolving them.** Numeric/date → `REVISES` → `CORRECTS`; **negation and noun substitution → `ADD`, two rivals, no marker**. SOTA answer: Graphiti/Zep bi-temporal closure (`#14198`). | real classifier probe | HIGH |

**The causal chain — this is why local fixes keep regenerating the symptom:**

```text
D3 retry loop + live session writes
   → fill the single worker's queue (D4)
      → pre_handler 6–9 s
         + D1 inline embed 2–4 s
            + D2 sequential legs 1.2 s
               = beyond the 10 s wait bound (owner decision #3834) → 504
```

D5 refuses writes once the corpus outgrows the graph; D6 hides all of it behind deploy churn.

---

## 3. What the comparables say the destination looks like

Four of the five have moved their expensive write work behind a queue; we already match two of the four moves.

| move | Mem0 | Zep | Hindsight | Supermemory | turbopuffer | us |
|---|---|---|---|---|---|---|
| durable cheap store | vector store primary; SQL = ADD history log | — | **Postgres+pgvector** | — | object storage | ✅ record |
| derived hot layer in front | vector+entity | **hot-graph cache** | servers as disposable caches | **KV hot layer** | NVMe cache | ✅ graph + eviction executor |
| **expensive work at write, behind a queue** | **<50 ms ack, queue, scale on depth** | async ingest | **"cost shifted to writes"** | **"dreaming" phase** | — | ❌ inline |
| **parallel legs, bounded traversal** | **4 signals scored in parallel** | hybrid (legs not stated) | **4 legs in parallel** | *not recorded* | — | ❌ sequential |
| **published retrieval latency** | 100–150 ms | p95 <100 ms | **recall 100–600 ms** | sub-300 ms | **14 ms warm / 500 ms cold** | ❌ none published |

⚠️ **Every figure in that table is VENDOR-PUBLISHED, not reproduced here.** They establish the *class* of latency the market ships; they are never our own measurement. Only turbopuffer publishes a **cold/warm split** — the others publish a single retrieval latency.

**What the table actually supports, stated exactly:** the *derived hot layer* is universal (**5 of 5**); *expensive work behind a queue at write time* is near-universal (**4 of 5** — Mem0, Zep, Hindsight, Supermemory); a *durable cheap source of truth* is **2 of 5** (Hindsight, turbopuffer) — Mem0's *primary* fact storage is its vector store with SQL an ADD history log, so calling it a cheap store would be our inference, not its documentation; **parallel retrieval legs are 2 of 5** (Hindsight's four legs; Mem0's four signals, scored in parallel and rank-fused). The destination is strongly evidenced for the write-side move and the hot layer, and rests on **two comparables** for the read-side move — still the thinnest of the four moves, and not a consensus.

**The gap is scheduling, not hardware.** That is the thesis of this plan, and it is why most of the scaling work below is free.

---

## 4. Two tracks, parallel

### Track F — make it work reliably *(cheap, immediate; F = fix)*

**F0 · Stabilise — free, days.** Stop the retry storm; serialise the capture drain; find the machine POSTing ~36 sessions/min;
verify allowance headroom before any drain. *Exit:* 429s ≈ 0, spool shrinking, 20/20 searches 200.

**F1 · Read path — free, ~1–2 weeks.** Parallel legs (`#14192`) · embedder seam + query micro-batching with a hard max-wait
(`#14186`) · stage timings on success (`#14187`). *Exit:* p50 ≤ 500 ms, p95 ≤ 1 s over 20 calls, fusion rank-identical.

**F2 · Write path — free, ~1 week.** Projection consumes `journal_event` on a worker loop (`#14185`), and the
remaining synchronous capture-path I/O leaves the loop (`#4625`). *Exit:* write ack ≤ 100 ms p95; projection lag bounded
and exported; a 200 s loopback `/health` soak inside the machine shows **no stall > 0.4 s** (that soak is `#4625`'s own
acceptance bar).

> **`#4625` status, checked 2026-11-21 and NOT assumed:** its two dominant signatures are already off-loop —
> `_session_recording_allowed` now runs via `_session_recording_allowed_off_loop` (`hosted_api.py:20177`, whose comment
> records that it used to run inline), and the capture write goes through `_CAPTURE_EXECUTOR` (`:21725`, `:21773`).
> What remains is *declared* rather than silent: the receipt's **registry CAS still constructs an SDK handle inside a
> pooled worker** (`:21707` carries the `#14086` review's P2 residual), and the `#3498` offload-inventory guard **cannot
> see `mcp_server.py`** or sync-helper indirection. So it is **not** a duplicate of `#14185` and must not be closed as one;
> it is the capture-path instance of the same class, re-scoped to those two residuals.

**F3 · Semantics — free, ~1 week.** Bi-temporal closure for negation/substitution (`#14198`); current-vs-history read rule
declared per read (`#2952`). *Exit:* all four pair shapes resolve; **the demo runs**, numeric and noun-substitution variants.

### Track S — build the destination *(starts now; spend only when a gate says so; S = scale)*

Track S is **not** blocked on Track F. Its first three items are free and independent, and doing them now is exactly what
makes the later spend cheap.

| item | cost | why now |
|---|---|---|
| **S1 Stateless API tier** — move the model out of the API process; drop the `/data` dependency | free | every comparable runs disposable servers. This is the prerequisite for cheap replicas, and it is a refactor that only gets harder once more depends on it |
| **S2 Define the scaling unit in writing** — "N tenants' working sets + 1 embedder + 1 pool", with the per-tenant footprint from M5 (`#14015`) | free | turns "how many machines" into a measured number |
| **S3 Latency budget per stage, published** — the numbers we intend to defend (Hindsight's 100–600 ms class) | free | you cannot hold a budget you never wrote down |
| **S4 Land the nine `#1677` guards** | free | unblocks using the cores we already pay for. The scope is the **nine rows**, not three — the rate-limit buckets and the model pre-warm (≈196 s cold, `embeddings.py:885-910`) at ~N× RSS are what make N workers expensive on 4096 MB |
| **S5 Dedicated CPU** on the API box | ≈ +$8/mo | removes burst throttling (0.24 s ↔ 2.07 s spread on identical work) |
| **S6 Second instance for survivability** | ≈ +$25/mo | a restart stops being an outage. After `#14067` (snapshot invalidation, merged) |
| **S7 Embedder as its own deployment** (S1's payoff) | ~+$25/mo | API never embeds inline; scale it independently |
| **S8 Replicas scaled on queue depth** (S1+S3 payoff) | variable | the Mem0 posture |
| **S9 Keyword leg to Postgres** (`#14000`), tokenisation first | free | cost per GB of searchable text drops 283–584× |

**Sequencing rule (cheap first, not "wait for permission"):** S1–S4 proceed **in parallel with** Track F because they are free and they are the foundation. S5–S9 wait only on **a measurement**, never on "we don't need it yet".

---

## 5. Gates — the only places spend is authorised

| gate | condition | authorises |
|---|---|---|
| **G0** | 429s ≈ 0 · spool shrinking · 20/20 reads 200 with p50/p95 recorded | trustworthy measurement at all |
| **G1** | p50 ≤ 500 ms · p95 ≤ 1 s · fusion rank-identical | S5 (dedicated CPU) — if still needed |
| **G2** | write ack ≤ 100 ms p95 · projection lag bounded | S6 (second instance) — HA, not speed |
| **G3** | the demo passes, both variants | beta release |
| **G4** | kill one instance → zero failed requests; N workers, no duplicate daemons | S7/S8 at scale |

---

## 6. The diagnostic programme — each probe serves a decision

| probe | design | decision |
|---|---|---|
| **P1** | 20 sequential searches, idle box, per-stage p50/p95 | is the read path fixed? (G1) |
| **P2** | N = 1, 5, 20 concurrent searches | how many users one instance carries (S2, S6) |
| **P3** | one write: ack vs time-to-visible | is F2 working? (G2) |
| **P4** | spool depth every 60 s for 10 min | is the backlog shrinking? (G0) |
| **P5** | allowance used vs cap, before/after a drain | safe to write? |
| **P6** | the four contradiction pair shapes | does memory resolve? (G3) |
| **P7** | two sessions + one contradiction + next turn | the product claim |

No measurement is taken "to see". Each exists to gate a phase or size a decision.

---

## 7. Deliberate non-goals

- ⛔ Raising the 10 s wait bound (owner decision `#3834`) — it hides the defect.
- ⛔ Buying compute/memory before G0–G2 — the research says the box is adequate and the **order of operations** is wrong.
- ⛔ Deleting customer data for headroom.
- ⛔ A new vector engine: Hindsight runs Postgres + pgvector, the stack we already chose (`STORAGE-ARCHITECTURE.md` §12.1c).

## 8. Open decisions (owner)

1. **The ~36 session-writes/min source** — wanted traffic during beta, or a runaway loop to stop?
2. **The 916-entry spool** — drain into the graph (demo prerequisite) or discard as stale and start clean?
3. **`#1677` guards** — pull S4 forward now (free, unblocks the cores we pay for) or leave it in Track S order?
