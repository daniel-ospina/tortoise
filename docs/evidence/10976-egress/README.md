---
title: "#10976 — cache EGRESS: the measurement"
type: evidence
domain: platform
doc_status: live
created: 2026-11-06
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph, FalkorDB cache, cache eviction
aboutObjects: "#10976, #11167, #10593, epic #7879, #11004, tortoise/cache_evictor.py, tortoise/cache_eviction.py, tools/cache_evict.py, tools/cache_eviction_census.py, docs/architecture/STORAGE-ARCHITECTURE.md"
---

# #10976 — cache EGRESS: the measurement

The issue's mandate is *"code that bounds the cache + a red-first test + the measurement
that proves it falls"*. This directory is the third part.

Run it. ⚠️ `tools/test_lane.py run` **cannot** run this probe — it invokes *pytest*,
which collects 0 items here. Start a lane and run the probe with the URI in the
environment:

```bash
uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"   # start + export
uv run python docs/evidence/10976-egress/probe_egress.py --out /tmp/egress.json
```

The probe mints its **own** graph (`egress_probe_<rand>`) and drops it, so it never
touches a graph anyone reads.

## ⛔ What this measurement is, and what it is not

| | |
|---|---|
| **measures** | the EXECUTOR (`tortoise/cache_evictor.py`): does a licensed, record-gated pass bound the cache, and does it hold back every node the probe refuses? Indicators **(a)** and **(c)**, plus the safety invariant. |
| **does NOT measure** | indicator **(b)** — "an evicted node is served from the record". A stand-in leg that holds every key cannot miss, so there is nothing to measure until the record can serve. |
| **also does not measure** | the record's readiness. `#10593` (hosted record writes rejected `22P02`) and `#7879` are **open**, which is why `tortoise/cache_eviction.py` answers `REFUSED` in production and why `record_leg` and `parity_report` in the output are labelled `stand-in` / `synthetic`. |

A measurement that hid that would be the "false PASS" the census exists to prevent.

## The run (falkordb 4.20.6, private lane container, `noeviction`)

`--seed 4000`, node budget = resident ÷ 3.

| | before | after |
|---|---|---|
| `:Point` turn-shaped nodes | **4,000** | **1,333** |
| `INFO memory used_memory` (exact) | **4,948,344 B** (4.72 M) | **3,944,920 B** (3.76 M) |
| `GRAPH.MEMORY USAGE` (sampling estimate, 100 samples × 3 repeats) | **1.0 MB** | **0.0 MB** |

- evicted **2,667** nodes toward a budget of **1,333** — the budget was **reached**
  (`verdict: EVICTED`, 6 passes).
- **`census_verdict: CLEAN`** — and the pass ran anyway, which is the point. `CLEAN`
  is the census's **byte** state ("within the declared byte ceiling"); the bound this
  pass enforces is **entries**. Gating the licence on the byte verdict made the entry
  bound unenforceable in exactly this population shape (many small turn nodes), so the
  census now publishes `plan["evictable"]` on **every** verdict and the consumer applies
  its own unit. No ceiling is fabricated to force a `PLAN`: `ceiling=None` is the
  instance's own `maxmemory`.
⚠️ **This run is reproducible, and the byte figure moves between runs** — the graph is
minted fresh each time and the allocator's baseline is not. An earlier run measured
**4,691,328 B → 3,762,072 B (−19.8%)** for the same 4,000 → 1,333 nodes, so the byte
delta has been observed at **−19.8 % to −20.3 %**; the *node* count is exact each time. The
*node* count is deterministic; the byte delta is a range, which is the same reason the
enforced bound is entries and bytes are only reported.

- **1,003,424 B / 2,667 nodes ≈ 376 B returned per evicted node** — *more* than the
  ~156 B/node the graph allocated on creation. That is allocator noise in the other
  direction, and it is exactly why one measured per-node figure must not be read as a
  cost: node deletion returns *some* memory, not a predictable amount, and the index
  share never comes back.

### Indicator (a) — the resident size actually falls

✅ `used_memory` 4.72 MB → 3.76 MB (**−1,003,424 B, −20.3%**) while the node count fell by
two thirds. The byte drop is **sub-linear** in the node drop, and that is the measured
finding behind the executor's choice of bound: creating 20,000 nodes on this container
raised `used_memory` by 3,113,176 B; deleting **all** of them returned only 1,368,808 B
— **44%**. Index memory and allocator retention are not returned by a node delete, so a
byte ceiling is a **lagging** signal that a sweeper cannot drive (it would either spin
or empty the graph). The enforced bound is therefore `TORTOISE_CACHE_MAX_NODES`; bytes
stay the **reporting** unit, and `byte_ceiling_still_exceeded` says plainly when node
eviction cannot satisfy a byte ceiling.

### Indicator (b) — an evicted node is served from the record

⛔ **NOT MEASURED HERE — `invariants.b_every_evicted_served` is `null`, not `true`.**
The stand-in record leg is built from **every** seeded key, so a lookup in it cannot
miss: reporting "50/50 served" as a green indicator would be a tautology dressed as a
measurement — a false PASS, the exact failure class the census exists to prevent. The
copy check is still recorded under its own honest key
(`recall.all_served_THE_TAUTOLOGY_NOT_A_MEASUREMENT`) so a reader can see what was, and
was not, established. A real reading needs the Postgres record, which `#10976` gates on
`#10593` + `#7879` — both open.

### Indicator (c) — the meter reads the cache's real size

✅ Both numbers are reported side by side, and the gap is the point:

- `INFO memory used_memory` — the instance's own **exact** figure.
- `GRAPH.MEMORY USAGE` — a **sampling estimate**, and **blind to the HNSW vector
  index** (#5331; measured 8.5×–21.7× under-report on the index share, ~2× on the
  total). `tortoise/graph_storage.py::PRECISION_NOTE` travels with every reading, and
  the probe reports it verbatim in `meter.precision_note`.

⚠️ **`GRAPH.MEMORY USAGE` reports whole MB**, so on a 4,000-node graph it reads
1.0 → 0.0. It is not a fine-grained instrument at this size; `used_memory` is the number
that moves. That is a property of the meter, stated rather than smoothed over.

### The invariant that makes it safe — **0 evictions of a node the record cannot serve**

✅ `safety.unservable_declared: 572`, `safety.unservable_evicted: []`,
`safety.unservable_all_still_resident: true`.

Every 7th node was declared unservable by the record leg. **445 nodes were held back**
by the per-node probe across this run's 6 passes (822 at the 16,000-node seed — the
figure is a per-run count, bounded by that run's declared-unservable count), **none** of
them was deleted, and the pass still reached its budget — so a cold head of unservable
nodes does not starve the sweep (the selector window doubles; the executor's own comment
records the starvation defect that test caught).

### The runaway guard, measured on a larger graph

At `--seed 16000` the same probe returns **`PARTIAL`**: 4,928 evicted, then *"still 5,739
node(s) over the budget after the full 10-pass budget — re-run to continue"*. One call
takes at most `max_passes × batch_size` (10 × 500). That is the cap working: `used_memory`
still fell **8.59 MB → 5.64 MB (−34%)**, `GRAPH.MEMORY USAGE` **3.0 → 2.0 MB**, and
**0** unservable nodes were evicted. A pass that cannot finish in one call reports it
rather than unbounded-looping.

## What is still NOT delivered (named, not implied)

1. **The record leg is a stand-in.** Indicator (b) is measured against a dict, because
   the real record cannot serve while `#10593`/`#7879` are open. The probe refuses to
   claim otherwise.
2. **Only the `turn` population has a parity instrument** (`tools/turn_parity_census.py`).
   Every other class is `cannot-answer` and therefore unevictable — a finding of the
   census (`#11004`), unchanged here.
3. **Nothing calls the pass on a schedule yet.** `tools/cache_evict.py` is the operator
   verb (dry run by default); wiring it into a periodic sweep is the remaining step, and
   it is blocked on the same record gates: a scheduled pass today would refuse on every
   run, which is correct but is not "evicted on a schedule".
